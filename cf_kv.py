import json
import os
from urllib.parse import quote
import requests


class CloudflareKV:
    """透過 Cloudflare REST API 讀寫私有 KV Namespace 資料（絕不印出 Token 或內容）。"""

    def __init__(self, account_id: str, namespace_id: str, api_token: str):
        self.account_id = account_id.strip()
        self.namespace_id = namespace_id.strip()
        self.api_token = api_token.strip()

        if not self.account_id or not self.namespace_id or not self.api_token:
            raise ValueError("Cloudflare KV 設定不完整（需要 account_id、namespace_id、api_token）。")

        self.base_url = (
            f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}"
            f"/storage/kv/namespaces/{self.namespace_id}/values"
        )
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {self.api_token}"})

    @classmethod
    def from_env(cls) -> "CloudflareKV":
        """從環境變數 CF_ACCOUNT_ID / CF_KV_NAMESPACE_ID / CF_API_TOKEN 建立 KV 客戶端。"""
        missing = [
            name
            for name in ("CF_ACCOUNT_ID", "CF_KV_NAMESPACE_ID", "CF_API_TOKEN")
            if not os.getenv(name, "").strip()
        ]
        if missing:
            raise RuntimeError(f"缺少 Cloudflare KV 環境變數：{', '.join(missing)}")

        return cls(
            account_id=os.getenv("CF_ACCOUNT_ID", ""),
            namespace_id=os.getenv("CF_KV_NAMESPACE_ID", ""),
            api_token=os.getenv("CF_API_TOKEN", ""),
        )

    def _key_url(self, key: str) -> str:
        return f"{self.base_url}/{quote(key, safe='')}"

    def get_text(self, key: str):
        """讀取文字型 KV 值；若鍵不存在 (404) 則回傳 None。"""
        resp = self.session.get(self._key_url(key), timeout=20)
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise RuntimeError(f"Cloudflare KV GET '{key}' 失敗 (HTTP {resp.status_code})")
        resp.encoding = "utf-8"
        return resp.text

    def put_text(self, key: str, text: str):
        """寫入 UTF-8 文字型 KV 值。"""
        resp = self.session.put(
            self._key_url(key),
            data=text.encode("utf-8"),
            headers={"Content-Type": "text/plain; charset=utf-8"},
            timeout=20,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Cloudflare KV PUT '{key}' 失敗 (HTTP {resp.status_code})")

    def get_json(self, key: str, default=None):
        """讀取並解析 JSON 型 KV 值；若鍵不存在則回傳 default。"""
        raw = self.get_text(key)
        if raw is None or not raw.strip():
            return default
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Cloudflare KV 鍵 '{key}' 的 JSON 格式異常：{e}") from e

    def put_json(self, key: str, value):
        """將物件序列化為 UTF-8 JSON 後寫入 KV。"""
        payload = json.dumps(value, ensure_ascii=False)
        self.put_text(key, payload)
