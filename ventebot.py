import os
from decimal import Decimal, InvalidOperation
import httpx

class VenteBotError(Exception):
    pass

class VenteBotClient:
    def __init__(self):
        self.base_url = (os.getenv("VENTE_BASE_URL") or "").rstrip("/")
        self.api_key = os.getenv("VENTE_API_KEY") or ""
        self.timeout = float(os.getenv("VENTE_TIMEOUT", "20"))

    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and "PASTE_" not in self.api_key and "YOUR_" not in self.base_url)

    def _headers(self):
        # VenteBot docs accept X-Reseller-Key or X-API-Key.
        return {"X-Reseller-Key": self.api_key, "Accept": "application/json"}

    async def _request(self, method: str, path: str, **kwargs):
        if not self.configured():
            raise VenteBotError("VenteBot API is not configured yet.")
        async with httpx.AsyncClient(base_url=self.base_url, headers=self._headers(), timeout=self.timeout) as client:
            try:
                r = await client.request(method, path, **kwargs)
                r.raise_for_status()
            except httpx.HTTPStatusError as e:
                detail = e.response.text[:300]
                raise VenteBotError(f"VenteBot returned HTTP {e.response.status_code}: {detail}") from e
            except httpx.HTTPError as e:
                raise VenteBotError(f"Could not reach VenteBot: {e}") from e
        try:
            return r.json()
        except ValueError as e:
            raise VenteBotError("VenteBot returned an invalid response.") from e

    async def me(self):
        return await self._request("GET", "/api/reseller/me")

    async def products(self, lang: str = "en"):
        return await self._request("GET", "/api/reseller/products", params={"lang": lang})

    async def quote(self, product_id: str, quantity: int = 1, activation_identifier: str | None = None):
        payload = {"product_id": product_id, "quantity": quantity}
        if activation_identifier:
            payload["activation_identifier"] = activation_identifier
        return await self._request("POST", "/api/reseller/quote", json=payload)

    async def create_order(self, product_id: str, quantity: int, activation_identifier: str | None,
                           customer_reference: str, idempotency_key: str):
        payload = {
            "product_id": product_id,
            "quantity": quantity,
            "customer_reference": customer_reference,
            "idempotency_key": idempotency_key,
        }
        if activation_identifier:
            payload["activation_identifier"] = activation_identifier
        return await self._request("POST", "/api/reseller/orders", json=payload)

    async def order(self, order_id: str):
        return await self._request("GET", f"/api/reseller/orders/{order_id}")


def extract_products(data):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("products", "data", "items", "results"):
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for nested in ("products", "items", "results"):
                if isinstance(value.get(nested), list):
                    return value[nested]
    return []


def money(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
