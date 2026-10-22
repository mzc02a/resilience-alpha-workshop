"""Transport-only checks; no real face, no account creation, no bypass of face validation."""
import asyncio
import secrets

import httpx


async def main():
    async with httpx.AsyncClient(timeout=20) as client:
        fields = {"email": "transport-check@example.com", "first_name": "Test", "last_name": "Transport", "password": secrets.token_urlsafe(24)}
        response = await client.post("http://waf:8080/auth/register", data=fields,
            files={"image": ("test.jpg", b"\xff\xd8\xff" + b"0" * 80000, "image/jpeg")})
        assert response.status_code == 422, f"Expected application image validation (422), got {response.status_code}"
        print("80KB multipart reaches image validation: HTTP 422 (invalid image, no account created)")
        response = await client.post("http://waf:8080/auth/register", data=fields,
            files={"image": ("test.jpg", b"0" * (4 * 1024 * 1024 + 1), "image/jpeg")})
        assert response.status_code == 413, f"Expected image size rejection (413), got {response.status_code}"
        print("Image over 4MiB remains rejected: HTTP 413")
        response = await client.get("http://waf:8080/payments", params={"q": "<script>alert(1)</script>"})
        assert response.status_code == 403
        print("WAF XSS filtering still enabled: HTTP 403")


if __name__ == "__main__":
    asyncio.run(main())
