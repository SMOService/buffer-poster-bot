from __future__ import annotations

import aiohttp

from config import BUFFER_API, BUFFER_TOKEN, logger


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


async def fetch_channels() -> list[dict]:
    data = await buffer_query("query { account { organizations { channels { id name service } } } }")
    channels = []
    try:
        for org in data["data"]["account"]["organizations"]:
            for ch in org.get("channels", []):
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
    query = """
    query($cid:ChannelId!){
      channel(id:$cid){
        posts(filter:{status:scheduled},first:100){ edges{ node{ id dueAt } } }
      }
    }"""
    data = await buffer_query(query, {"cid": channel_id})
    try:
        return len(data["data"]["channel"]["posts"]["edges"])
    except (KeyError, TypeError):
        return None
