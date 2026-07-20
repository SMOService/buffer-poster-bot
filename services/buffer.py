from __future__ import annotations

import aiohttp

from config import BUFFER_API, BUFFER_TOKEN, logger


class BufferAuthError(Exception):
    """Buffer ответил FORBIDDEN на channels. Сам токен валиден (account читается),
    но доступа к каналам нет — обычно: истёкшая/слетевшая подписка ЛИБО у
    Personal Key (Buffer API) не выданы права на channels. Проверь то и другое."""


# Org id(s) кэшируем — нужны для root-запросов channels(input) / posts(input)
# в новом Buffer API. У single-user аккаунта обычно одна организация.
_ORG_IDS_CACHE: list[str] = []


def _is_forbidden(data: dict) -> bool:
    for err in data.get("errors") or []:
        code = (err.get("extensions") or {}).get("code")
        msg = (err.get("message") or "").lower()
        if code == "FORBIDDEN" or "not authorized" in msg:
            return True
    return False


async def buffer_query(query: str, variables: dict | None = None) -> dict:
    payload: dict = {"query": query}
    if variables:
        payload["variables"] = variables
    async with aiohttp.ClientSession() as s:
        async with s.post(
            BUFFER_API,
            json=payload,
            headers={
                "Authorization": f"Bearer {BUFFER_TOKEN}",
                "Content-Type": "application/json",
            },
        ) as resp:
            return await resp.json()


def _raise_forbidden(data: dict, where: str) -> None:
    logger.warning("%s: Buffer FORBIDDEN | %s", where, data)
    raise BufferAuthError(
        "Buffer не отдаёт каналы (FORBIDDEN). Токен живой, но прав на channels нет — "
        "проверь подписку и права Personal Key: publish.buffer.com → Buffer API."
    )


async def _organization_ids(force: bool = False) -> list[str]:
    global _ORG_IDS_CACHE
    if _ORG_IDS_CACHE and not force:
        return _ORG_IDS_CACHE
    data = await buffer_query("query { account { organizations { id } } }")
    if _is_forbidden(data):
        _raise_forbidden(data, "organization_ids")
    try:
        _ORG_IDS_CACHE = [o["id"] for o in data["data"]["account"]["organizations"]]
    except (KeyError, TypeError) as e:
        logger.error("organization_ids error: %s | %s", e, data)
        return []
    return _ORG_IDS_CACHE


async def fetch_channels() -> list[dict]:
    """Новый Buffer API: каналы тянутся root-запросом channels(input:{organizationId}).
    Старый путь account.organizations.channels задеприкейчен и отдаёт FORBIDDEN."""
    channels: list[dict] = []
    q = "query($i:ChannelsInput!){ channels(input:$i){ id name service } }"
    for org_id in await _organization_ids():
        data = await buffer_query(q, {"i": {"organizationId": org_id}})
        if _is_forbidden(data):
            _raise_forbidden(data, "fetch_channels")
        try:
            for ch in data["data"]["channels"] or []:
                channels.append(
                    {
                        "id": ch["id"],
                        "name": ch.get("name") or ch["service"],
                        "service": ch["service"],
                    }
                )
        except (KeyError, TypeError) as e:
            logger.error("fetch_channels error: %s | %s", e, data)
    return channels


async def create_post(
    channel_id: str,
    text: str,
    image_urls: list[str],
    due_at: str,
) -> dict:
    """Schema drift 2026: assets is required [AssetInput!]!."""
    assets = [{"image": {"url": u}} for u in image_urls[:4]]
    mutation = """
    mutation CreatePost($cid:ChannelId!,$text:String!,$due:DateTime,$assets:[AssetInput!]!){
      createPost(input:{channelId:$cid,text:$text,schedulingType:automatic,
        mode:customScheduled,dueAt:$due,assets:$assets}){
        ...on PostActionSuccess{post{id}}
        ...on MutationError{message}
      }
    }"""
    return await buffer_query(mutation, {"cid": channel_id, "text": text, "due": due_at, "assets": assets})


async def count_scheduled_posts(channel_id: str):
    """Новый Buffer API: запланированные посты — root posts(input:{organizationId,
    filter:{channelIds,status}}). Возвращает None, если посчитать не удалось
    (например, ключ не имеет доступа к этому каналу) — UI покажет '?'."""
    query = "query($i:PostsInput!){ posts(input:$i, first:100){ edges{ node{ id } } } }"
    for org_id in await _organization_ids():
        data = await buffer_query(
            query,
            {"i": {"organizationId": org_id, "filter": {"channelIds": [channel_id], "status": ["scheduled"]}}},
        )
        try:
            return len(data["data"]["posts"]["edges"])
        except (KeyError, TypeError):
            continue
    return None
