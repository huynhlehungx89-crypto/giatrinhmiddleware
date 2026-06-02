import hashlib
import logging
import xmlrpc.client
from contextvars import ContextVar
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15

_request_client: ContextVar[Optional["OdooClient"]] = ContextVar(
    "_request_odoo_client", default=None
)
_process_client_cache: Dict[str, "OdooClient"] = {}


class TimeoutTransport(xmlrpc.client.Transport):
    """XML-RPC transport with socket timeout."""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT):
        self.timeout = timeout
        super().__init__()

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self.timeout
        return conn


class OdooClient:
    """XML-RPC client with search_read and session reuse."""

    def __init__(
        self,
        url: str,
        database: str,
        username: str,
        api_key: str,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.url = url.rstrip("/")
        self.database = database
        self.username = username
        self.password = api_key
        self.timeout = timeout
        self.uid: Optional[int] = None
        self._models = None
        self._connected = False
        self._company_cache: Optional[Dict] = None

    def connect(self) -> None:
        if self._connected and self.uid is not None and self._models is not None:
            return
        transport = TimeoutTransport(self.timeout)
        common = xmlrpc.client.ServerProxy(
            f"{self.url}/xmlrpc/2/common",
            transport=transport,
            allow_none=True,
        )
        uid = common.authenticate(
            self.database, self.username, self.password, {}
        )
        if not uid:
            raise ConnectionError(
                "Xác thực Odoo thất bại. Kiểm tra URL, database, username và API key."
            )
        self.uid = int(uid)
        self._models = xmlrpc.client.ServerProxy(
            f"{self.url}/xmlrpc/2/object",
            transport=TimeoutTransport(self.timeout),
            allow_none=True,
        )
        self._connected = True

    def execute_kw(
        self,
        model: str,
        method: str,
        args: List,
        kwargs: Optional[Dict] = None,
    ) -> Any:
        if self._models is None or self.uid is None:
            raise ConnectionError("Odoo client chưa được kết nối.")
        return self._models.execute_kw(
            self.database,
            self.uid,
            self.password,
            model,
            method,
            args,
            kwargs or {},
        )

    def search_read(
        self,
        model: str,
        domain: List,
        fields: List[str],
        limit: int = 10,
        order: Optional[str] = None,
    ) -> List[Dict]:
        """Search and read records in one Odoo API call."""
        kwargs: Dict[str, Any] = {"fields": fields, "limit": limit}
        if order:
            kwargs["order"] = order
        return self.execute_kw(model, "search_read", [domain], kwargs) or []


def _settings_cache_key(settings: Dict) -> str:
    raw = "|".join(
        [
            settings.get("url", ""),
            settings.get("database", ""),
            settings.get("username", ""),
        ]
    )
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def get_odoo_settings() -> Optional[Dict]:
    import database

    conn = database.get_db()
    row = conn.execute(
        "SELECT url, database, username, api_key FROM odoo_settings WHERE id = 1"
    ).fetchone()
    conn.close()
    if not row:
        return None
    settings = dict(row)
    if all(settings.get(k) for k in ("url", "database", "username", "api_key")):
        return settings
    return None


def create_odoo_client() -> OdooClient:
    settings = get_odoo_settings()
    if not settings:
        raise ValueError("Odoo settings chưa được cấu hình.")

    cache_key = _settings_cache_key(settings)
    cached = _process_client_cache.get(cache_key)
    if cached is not None:
        cached.connect()
        return cached

    client = OdooClient(
        settings["url"],
        settings["database"],
        settings["username"],
        settings["api_key"],
    )
    client.connect()
    _process_client_cache[cache_key] = client
    return client


def get_odoo_client_for_request() -> OdooClient:
    """Reuse one authenticated client per HTTP request."""
    client = _request_client.get()
    if client is None:
        client = create_odoo_client()
        _request_client.set(client)
    return client


def clear_odoo_client_for_request() -> None:
    client = _request_client.get()
    if client is not None:
        client._company_cache = None
    _request_client.set(None)
