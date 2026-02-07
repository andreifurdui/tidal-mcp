"""
Authentication module for the TIDAL v2 API.

Handles two OAuth2 flows:
- Client Credentials: for catalog/search operations (no user login needed)
- Authorization Code PKCE: for user-scoped operations (favorites, playlists)

Tokens are persisted to a JSON file in the system temp directory so sessions
survive across MCP server restarts.
"""

import base64
import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlencode

import requests


TOKEN_URL = "https://auth.tidal.com/v1/oauth2/token"
AUTHORIZE_URL = "https://login.tidal.com/authorize"
TOKEN_FILE = Path(tempfile.gettempdir()) / "tidal-mcp-tokens.json"

# Scopes needed for user operations
PKCE_SCOPES = "user.read collection.read playlists.read playlists.write search.read"

# Buffer before token expiry to trigger refresh (seconds)
EXPIRY_BUFFER = 60

# Timeout for PKCE browser login (seconds)
LOGIN_TIMEOUT = 300


class _CallbackHandler(BaseHTTPRequestHandler):
    """HTTP request handler that captures the OAuth callback code."""

    auth_code: Optional[str] = None
    state: Optional[str] = None
    error: Optional[str] = None

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs

        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/callback":
            _CallbackHandler.auth_code = params.get("code", [None])[0]
            _CallbackHandler.state = params.get("state", [None])[0]
            _CallbackHandler.error = params.get("error", [None])[0]

            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()

            if _CallbackHandler.auth_code:
                body = (
                    "<html><body><h2>TIDAL Login Successful</h2>"
                    "<p>You can close this window and return to your terminal.</p>"
                    "</body></html>"
                )
            else:
                error_msg = _CallbackHandler.error or "Unknown error"
                body = (
                    f"<html><body><h2>TIDAL Login Failed</h2>"
                    f"<p>Error: {error_msg}</p></body></html>"
                )
            self.wfile.write(body.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass


class TidalAuth:
    """Manages authentication tokens for the TIDAL v2 API."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        token_file: Path = TOKEN_FILE,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.token_file = token_file

        # Client credentials token (for catalog/search)
        self._client_token: Optional[str] = None
        self._client_token_expires_at: float = 0

        # User tokens (for favorites, playlists)
        self._user_access_token: Optional[str] = None
        self._user_refresh_token: Optional[str] = None
        self._user_token_expires_at: float = 0
        self._user_id: Optional[str] = None

        # Try loading persisted tokens
        self._load_tokens()

    # ── Client Credentials Flow ──────────────────────────────────────

    def get_client_token(self) -> str:
        """Get a valid client credentials token, refreshing if needed.

        Returns:
            A bearer access token string.

        Raises:
            RuntimeError: If token request fails.
        """
        if self._client_token and time.time() < self._client_token_expires_at:
            return self._client_token

        self._request_client_token()
        return self._client_token

    def _request_client_token(self) -> None:
        """Request a new client credentials token from TIDAL."""
        response = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"Failed to get client token: {response.status_code} {response.text}"
            )

        data = response.json()
        self._client_token = data["access_token"]
        self._client_token_expires_at = time.time() + data.get("expires_in", 86400) - EXPIRY_BUFFER

    # ── Authorization Code PKCE Flow ─────────────────────────────────

    def login_pkce(self, fn_print: Callable[[str], None] = print) -> bool:
        """Authenticate the user via the Authorization Code PKCE flow.

        Tries in order:
        1. Load existing tokens from file — if valid, return immediately.
        2. Refresh expired tokens using refresh_token — if successful, return.
        3. Open browser for full PKCE login flow.

        Args:
            fn_print: Function to display status messages.

        Returns:
            True if authentication succeeded, False otherwise.
        """
        # Try existing valid tokens
        if self.is_user_authenticated():
            fn_print("TIDAL session is valid (loaded from saved tokens).")
            return True

        # Try refreshing expired tokens
        if self._user_refresh_token:
            fn_print("Refreshing TIDAL session...")
            if self._refresh_user_token():
                fn_print("TIDAL session refreshed successfully.")
                return True
            fn_print("Token refresh failed. Starting new login...")

        # Full PKCE flow
        return self._do_pkce_login(fn_print)

    def _do_pkce_login(self, fn_print: Callable[[str], None] = print) -> bool:
        """Execute the full PKCE authorization flow.

        Opens a browser for user login and starts a local callback server
        to receive the authorization code.
        """
        # Generate PKCE pair
        code_verifier = secrets.token_urlsafe(64)[:128]
        code_challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(code_verifier.encode()).digest()
            )
            .rstrip(b"=")
            .decode()
        )
        state = secrets.token_urlsafe(32)

        # Build authorization URL
        auth_params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": PKCE_SCOPES,
            "code_challenge_method": "S256",
            "code_challenge": code_challenge,
            "state": state,
        }
        auth_url = f"{AUTHORIZE_URL}?{urlencode(auth_params)}"

        # Reset callback handler state
        _CallbackHandler.auth_code = None
        _CallbackHandler.state = None
        _CallbackHandler.error = None

        # Parse port from redirect URI
        from urllib.parse import urlparse
        parsed_uri = urlparse(self.redirect_uri)
        port = parsed_uri.port or 18888

        # Start local callback server
        try:
            server = HTTPServer(("localhost", port), _CallbackHandler)
        except OSError as e:
            fn_print(f"Failed to start callback server on port {port}: {e}")
            fn_print(f"Try changing TIDAL_REDIRECT_PORT to a different port.")
            return False

        server.timeout = LOGIN_TIMEOUT

        fn_print(f"Opening browser for TIDAL login (expires in {LOGIN_TIMEOUT}s)...")
        webbrowser.open(auth_url)

        # Wait for callback in a thread so we can enforce timeout
        received = threading.Event()

        def serve():
            while not received.is_set():
                server.handle_request()
                if _CallbackHandler.auth_code or _CallbackHandler.error:
                    received.set()

        server_thread = threading.Thread(target=serve, daemon=True)
        server_thread.start()
        received.wait(timeout=LOGIN_TIMEOUT)
        server.server_close()

        if not _CallbackHandler.auth_code:
            error = _CallbackHandler.error or "Login timed out"
            fn_print(f"TIDAL login failed: {error}")
            return False

        if _CallbackHandler.state != state:
            fn_print("TIDAL login failed: state mismatch (possible CSRF)")
            return False

        # Exchange code for tokens
        fn_print("Exchanging authorization code for tokens...")
        return self._exchange_code(
            _CallbackHandler.auth_code, code_verifier, fn_print
        )

    def _exchange_code(
        self, auth_code: str, code_verifier: str, fn_print: Callable
    ) -> bool:
        """Exchange an authorization code for access and refresh tokens."""
        response = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "redirect_uri": self.redirect_uri,
                "client_id": self.client_id,
                "code_verifier": code_verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if response.status_code != 200:
            fn_print(f"Token exchange failed: {response.status_code} {response.text}")
            return False

        data = response.json()
        self._user_access_token = data["access_token"]
        self._user_refresh_token = data.get("refresh_token")
        self._user_token_expires_at = (
            time.time() + data.get("expires_in", 86400) - EXPIRY_BUFFER
        )

        # Extract user ID from response if available
        user_data = data.get("user", {})
        if isinstance(user_data, dict):
            self._user_id = str(user_data.get("userId", ""))

        self._save_tokens()
        fn_print("TIDAL login successful!")
        return True

    def _refresh_user_token(self) -> bool:
        """Refresh the user access token using the refresh token.

        Returns:
            True if refresh succeeded, False otherwise.
        """
        if not self._user_refresh_token:
            return False

        response = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self._user_refresh_token,
                "client_id": self.client_id,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if response.status_code != 200:
            # Refresh failed — clear tokens so next login starts fresh
            self._user_access_token = None
            self._user_refresh_token = None
            self._user_token_expires_at = 0
            self._save_tokens()
            return False

        data = response.json()
        self._user_access_token = data["access_token"]
        # Some providers return a new refresh token
        if "refresh_token" in data:
            self._user_refresh_token = data["refresh_token"]
        self._user_token_expires_at = (
            time.time() + data.get("expires_in", 86400) - EXPIRY_BUFFER
        )

        self._save_tokens()
        return True

    # ── Token Access ─────────────────────────────────────────────────

    def get_user_token(self) -> str:
        """Get a valid user access token, refreshing if needed.

        Returns:
            A bearer access token string.

        Raises:
            RuntimeError: If no valid token is available.
        """
        if self._user_access_token and time.time() < self._user_token_expires_at:
            return self._user_access_token

        # Try refreshing
        if self._user_refresh_token and self._refresh_user_token():
            return self._user_access_token

        raise RuntimeError(
            "No valid TIDAL user token. Please login first using tidal_login()."
        )

    def is_user_authenticated(self) -> bool:
        """Check if there is a valid (or refreshable) user session.

        Returns:
            True if the user has valid tokens or they can be refreshed.
        """
        # Valid token exists
        if self._user_access_token and time.time() < self._user_token_expires_at:
            return True

        # Try refreshing silently
        if self._user_refresh_token:
            return self._refresh_user_token()

        return False

    @property
    def user_id(self) -> Optional[str]:
        """The authenticated user's TIDAL user ID, or None."""
        return self._user_id

    @user_id.setter
    def user_id(self, value: str):
        self._user_id = value
        self._save_tokens()

    # ── Token Persistence ────────────────────────────────────────────

    def _save_tokens(self) -> None:
        """Persist tokens to the token file."""
        data = {}

        if self._user_access_token:
            data["user_token"] = {
                "access_token": self._user_access_token,
                "refresh_token": self._user_refresh_token,
                "expires_at": self._user_token_expires_at,
            }

        if self._user_id:
            data["user_id"] = self._user_id

        try:
            self.token_file.write_text(json.dumps(data, indent=2))
        except OSError as e:
            print(f"Warning: Could not save tokens: {e}")

    def _load_tokens(self) -> bool:
        """Load tokens from the token file.

        Returns:
            True if tokens were loaded, False otherwise.
        """
        if not self.token_file.exists():
            return False

        try:
            data = json.loads(self.token_file.read_text())
        except (json.JSONDecodeError, OSError):
            return False

        user_token = data.get("user_token", {})
        if user_token:
            self._user_access_token = user_token.get("access_token")
            self._user_refresh_token = user_token.get("refresh_token")
            self._user_token_expires_at = user_token.get("expires_at", 0)

        self._user_id = data.get("user_id")

        return bool(self._user_access_token)
