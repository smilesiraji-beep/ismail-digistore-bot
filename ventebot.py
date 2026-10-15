
import os
from decimal import Decimal, InvalidOperation

import httpx


class VenteBotError(Exception):
    pass


class VenteBotClient:
    def __init__(self):
        self.base_url = (
            os.getenv("SUPPLIER_API_BASE_URL") or ""
        ).strip().rstrip("/")

        self.api_key = (
            os.getenv("SUPPLIER_API_KEY") or ""
        ).strip()

        self.timeout = float(
            os.getenv("VENTE_TIMEOUT", "20")
        )

    def configured(self) -> bool:
        return bool(
            self.base_url
            and self.api_key
            and self.base_url.startswith(
                ("https://", "http://")
            )
            and "PASTE_" not in self.api_key
            and "YOUR_" not in self.api_key
            and "YOUR_" not in self.base_url
        )

    def _headers(self):
        return {
            "X-Reseller-Key": self.api_key,
            "Accept": "application/json",
        }

    async def _request(self, method: str, path: str, **kwargs):
        if not self.configured():
            raise VenteBotError(
                "Supplier API is not configured. "
                "Check SUPPLIER_API_BASE_URL and "
                "SUPPLIER_API_KEY in Railway Variables."
            )

        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._headers(),
                timeout=self.timeout,
            ) as client:
                response = await client.request(
                    method,
                    path,
                    **kwargs,
                )
                response.raise_for_status()

        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            detail = error.response.text[:300]

            raise VenteBotError(
                f"Supplier API returned HTTP {status}: {detail}"
            ) from error

        except httpx.HTTPError as error:
            raise VenteBotError(
                f"Could not reach Supplier API: {error}"
            ) from error

        try:
            return response.json()

        except ValueError as error:
            raise VenteBotError(
                "Supplier API returned an invalid JSON response."
            ) from error

    async def me(self):
        return await self._request(
            "GET",
            "/api/reseller/me",
        )

    async def products(self, lang: str = "en"):
        return await self._request(
            "GET",
            "/api/reseller/products",
            params={"lang": lang},
        )

    async def quote(
        self,
        product_id: str,
        quantity: int = 1,
        activation_identifier: str | None = None,
    ):
        payload = {
            "product_id": product_id,
            "quantity": quantity,
        }

        if activation_identifier:
            payload["activation_identifier"] = (
                activation_identifier
            )

        return await self._request(
            "POST",
            "/api/reseller/quote",
            json=payload,
        )

    async def create_order(
        self,
        product_id: str,
        quantity: int,
        activation_identifier: str | None,
        customer_reference: str,
        idempotency_key: str,
    ):
        payload = {
            "product_id": product_id,
            "quantity": quantity,
            "customer_reference": customer_reference,
            "idempotency_key": idempotency_key,
        }

        if activation_identifier:
            payload["activation_identifier"] = (
                activation_identifier
            )

        return await self._request(
            "POST",
            "/api/reseller/orders",
            json=payload,
        )

    async def order(self, order_id: str):
        return await self._request(
            "GET",
            f"/api/reseller/orders/{order_id}",
        )

    async def submit_activation_identifier(
        self,
        order_id: str,
        activation_identifier: str,
    ):
        return await self._request(
            "POST",
            (
                f"/api/reseller/orders/{order_id}"
                "/activation-identifier"
            ),
            json={
                "activation_identifier": (
                    activation_identifier
                )
            },
        )


def extract_products(data):
    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    for key in (
        "products",
        "data",
        "items",
        "results",
    ):
        value = data.get(key)

        if isinstance(value, list):
            return value

        if isinstance(value, dict):
            for nested in (
                "products",
                "items",
                "results",
            ):
                nested_value = value.get(nested)

                if isinstance(nested_value, list):
                    return nested_value

    return []


def money(value):
    try:
        return Decimal(str(value))

    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ):
        return None
