from authlib.integrations.starlette_client import OAuth

from app.config import (
    AUTHORITY,
    ENTRA_CLIENT_ID,
    ENTRA_CLIENT_SECRET,
    ENTRA_GROUP_ID,
)

oauth = OAuth()
oauth.register(
    name="entra",
    client_id=ENTRA_CLIENT_ID,
    client_secret=ENTRA_CLIENT_SECRET,
    server_metadata_url=f"{AUTHORITY}/.well-known/openid-configuration",
    client_kwargs={"scope": "openid profile email"},
)


def in_group(groups, group_id: str = ENTRA_GROUP_ID) -> bool:
    if isinstance(groups, str):
        groups = [groups]
    if not isinstance(groups, (list, tuple)):
        return False
    wanted = group_id.lower()
    return any(str(group).lower() == wanted for group in groups)


def groups_omitted(claims: dict) -> bool:
    claim_names = claims.get("_claim_names") or {}
    return isinstance(claim_names, dict) and "groups" in claim_names


def user_from_claims(claims: dict) -> dict:
    name = claims.get("name") or claims.get("preferred_username") or claims.get("email") or "Unknown"
    email = claims.get("preferred_username") or claims.get("email") or ""
    return {
        "oid": claims.get("oid") or claims.get("sub") or "",
        "name": name,
        "email": email,
    }
