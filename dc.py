from typing import Optional

import discord


async def fresh_attachment_url(client: discord.Client, clip) -> Optional[str]:
    channel = client.get_channel(clip["channel_id"])
    if channel is None:
        try:
            channel = await client.fetch_channel(clip["channel_id"])
        except discord.HTTPException:
            return None
    try:
        msg = await channel.fetch_message(clip["message_id"])
    except discord.HTTPException:
        return None
    for att in msg.attachments:
        if att.id == clip["attachment_id"]:
            return att.url
    return msg.attachments[0].url if msg.attachments else None
