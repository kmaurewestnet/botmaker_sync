"""Pydantic models mirroring Botmaker API response shapes (botmaker_api.json).

Enum-typed API fields (priority, status, from, content.type, ...) are kept as
plain `str` here, not Literal/Enum: this is a read-mirror, so a new enum value
Botmaker adds in the future should pass through, not raise.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


# ===== channels =====
# IChannelResponse is a oneOf over per-platform variants with no discriminator
# wrapper; modeled here as one flat optional superset since every variant
# lands in the same DB row shape anyway.
class ChannelModel(ApiModel):
    id: str | None = None
    platform: str | None = None
    active: bool | None = None
    name: str | None = None
    webhook_id: str | None = Field(None, alias="webhookId")
    number: str | None = None
    status: str | None = None
    quality: str | None = None
    waba_id: str | None = Field(None, alias="wabaId")
    trial: bool | None = None
    recipient_id: str | None = Field(None, alias="recipientId")
    days_to_expire: int | None = Field(None, alias="daysToExpire")
    token: str | None = None
    page_id: str | None = Field(None, alias="pageId")


class ChannelsListResponse(ApiModel):
    items: list[ChannelModel] = Field(default_factory=list)


# ===== agents =====
class AgentModel(ApiModel):
    id: str | None = None
    email: str | None = None
    name: str | None = None
    alias: str | None = None
    is_online: bool | None = Field(None, alias="isOnline")
    status: str | None = None
    role: str | None = None
    queues: list[str] = Field(default_factory=list)
    slots: int | None = None
    priority: str | None = None
    groups: list[str] = Field(default_factory=list)
    additional_info: dict | None = Field(None, alias="additionalInfo")
    creation_time: datetime | None = Field(None, alias="creationTime")


class AgentsPage(ApiModel):
    next_page: str | None = Field(None, alias="nextPage")
    items: list[AgentModel] = Field(default_factory=list)


# ===== contacts =====
class ContactField(ApiModel):
    value: str | None = None
    label: str | None = None


class ChatEntry(ApiModel):
    id: str | None = None
    platform_contact_id: str | None = Field(None, alias="platformContactId")
    chat_channel_id: str | None = Field(None, alias="chatChannelId")
    bsuid: str | None = None


class ContactModel(ApiModel):
    id: str | None = None
    first_name: str | None = Field(None, alias="firstName")
    last_name: str | None = Field(None, alias="lastName")
    birthday: str | None = None
    picture_url: str | None = Field(None, alias="pictureUrl")
    language: str | None = None
    country: str | None = None
    company_id: str | None = Field(None, alias="companyId")
    job_title: str | None = Field(None, alias="jobTitle")
    phone_numbers: list[ContactField] = Field(default_factory=list, alias="phoneNumbers")
    emails: list[ContactField] = Field(default_factory=list)
    addresses: list[ContactField] = Field(default_factory=list)
    websites: list[ContactField] = Field(default_factory=list)
    instagram_ids: list[str] = Field(default_factory=list, alias="instagramIds")
    facebook_ids: list[str] = Field(default_factory=list, alias="facebookIds")
    twitter_ids: list[str] = Field(default_factory=list, alias="twitterIds")
    notes: list[str] = Field(default_factory=list)
    chats: list[ChatEntry] = Field(default_factory=list)
    whatsapp_bsuids: list[str] = Field(default_factory=list, alias="whatsappBsuids")


class ContactsPage(ApiModel):
    next_page: str | None = Field(None, alias="nextPage")
    items: list[ContactModel] = Field(default_factory=list)


# ===== chats =====
class ChatReference(ApiModel):
    chat_id: str | None = Field(None, alias="chatId")
    channel_id: str | None = Field(None, alias="channelId")
    contact_id: str | None = Field(None, alias="contactId")


class ChatModel(ApiModel):
    chat: ChatReference | None = None
    creation_time: datetime | None = Field(None, alias="creationTime")
    last_session_creation_time: datetime | None = Field(None, alias="lastSessionCreationTime")
    external_id: str | None = Field(None, alias="externalId")
    first_name: str | None = Field(None, alias="firstName")
    last_name: str | None = Field(None, alias="lastName")
    country: str | None = None
    email: str | None = None
    whatsapp_window_close_datetime: datetime | None = Field(None, alias="whatsAppWindowCloseDatetime")
    variables: dict[str, str] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    queue_id: str | None = Field(None, alias="queueId")
    agent_id: str | None = Field(None, alias="agentId")
    on_hold_agent_id: str | None = Field(None, alias="onHoldAgentId")
    last_user_message_datetime: datetime | None = Field(None, alias="lastUserMessageDatetime")
    is_banned: bool | None = Field(None, alias="isBanned")
    is_tester: bool | None = Field(None, alias="isTester")
    is_bot_muted: bool | None = Field(None, alias="isBotMuted")


class ChatsPage(ApiModel):
    next_page: str | None = Field(None, alias="nextPage")
    items: list[ChatModel] = Field(default_factory=list)


# ===== sessions (= conversations) =====
class SessionMessageModel(ApiModel):
    id: str | None = None
    creation_time: datetime | None = Field(None, alias="creationTime")
    from_role: str | None = Field(None, alias="from")
    agent_id: str | None = Field(None, alias="agentId")
    queue_id: str | None = Field(None, alias="queueId")
    content: dict | None = None
    encryption_params: dict | None = Field(None, alias="encryptionParams")


class SessionEventModel(ApiModel):
    name: str | None = None
    creation_time: datetime | None = Field(None, alias="creationTime")
    info: dict | None = None


class SessionAspectScores(ApiModel):
    conciseness: int | None = None
    clarity: int | None = None
    empathy_tone: int | None = Field(None, alias="empathyTone")
    understanding: int | None = None
    resolution: int | None = None


class SessionAiAnalysisModel(ApiModel):
    summary: str | None = None
    does_not_meet_criteria: bool | None = Field(None, alias="doesNotMeetCriteria")
    name: str | None = None
    justification: str | None = None
    aspect_scores: SessionAspectScores | None = Field(None, alias="aspectScores")
    quality_score: int | None = Field(None, alias="qualityScore")


class SessionModel(ApiModel):
    id: str | None = None
    creation_time: datetime | None = Field(None, alias="creationTime")
    starting_cause: str | None = Field(None, alias="startingCause")
    chat: ChatModel | None = None
    messages: list[SessionMessageModel] = Field(default_factory=list)
    events: list[SessionEventModel] = Field(default_factory=list)
    ai_analysis: SessionAiAnalysisModel | None = Field(None, alias="aiAnalysis")


class SessionsPage(ApiModel):
    next_page: str | None = Field(None, alias="nextPage")
    items: list[SessionModel] = Field(default_factory=list)
