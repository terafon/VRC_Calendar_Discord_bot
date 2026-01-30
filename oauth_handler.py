from google_auth_oauthlib.flow import Flow

SCOPES = ['https://www.googleapis.com/auth/calendar']


class OAuthHandler:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self._client_config = {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }

    def generate_auth_url(self, state: str) -> str:
        """OAuth 認証 URL を生成"""
        flow = Flow.from_client_config(
            self._client_config,
            scopes=SCOPES,
            redirect_uri=self.redirect_uri,
        )
        auth_url, _ = flow.authorization_url(
            access_type='offline',
            prompt='consent',
            state=state,
        )
        return auth_url

    def exchange_code(self, code: str) -> dict:
        """認可コードをトークンに交換"""
        flow = Flow.from_client_config(
            self._client_config,
            scopes=SCOPES,
            redirect_uri=self.redirect_uri,
        )
        flow.fetch_token(code=code)
        credentials = flow.credentials
        return {
            "access_token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_expiry": credentials.expiry.isoformat() if credentials.expiry else None,
        }
