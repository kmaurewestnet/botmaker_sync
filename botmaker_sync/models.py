"""Pydantic models mirroring Botmaker API response shapes (botmaker_api.json).

Enum-typed API fields (priority, status, from, content.type, ...) are kept as
plain `str` here, not Literal/Enum: this is a read-mirror, so a new enum value
Botmaker adds in the future should pass through, not raise.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

logger = logging.getLogger(__name__)


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
    items: list[SessionModel] = Field(default_factory=list)


# ===== dashboards / agent metrics =====
def _metric_int(value: object) -> object:
    """Every numeric field of this endpoint is typed `str` in the spec and comes
    back quoted ("3358", "2"). Coerce once here, at the edge, so Metabase gets
    real integers instead of text to cast on every query.

    Two values are not numbers: the API sends "-" for a metric that does not
    apply (all of them, on a still-open session) and occasionally "". Both mean
    "no value" -> NULL.

    Anything else unparseable is logged and dropped to NULL rather than raised.
    This mirror runs unattended every 15 minutes, and a single odd value in one
    row should not abort the whole window -- same reasoning as keeping enum-typed
    fields as plain str above."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if text in ("", "-"):
            return None
        try:
            return int(text)
        except ValueError:
            logger.warning("agent metrics: dropping non-numeric value %r", value)
            return None
    return value


# Optional[int], not `int | None`: this alias is evaluated at import time (the
# module's `from __future__ import annotations` only defers annotations), and
# the sync runs on Python 3.9 in production, where `int | None` raises.
MetricInt = Annotated[Optional[int], BeforeValidator(_metric_int)]


# Free-text fields (queue, typification, agentName) stay str: they can also come
# back as "-", but that is preserved as-is rather than guessed at.
class AgentMetricModel(ApiModel):
    session_id: str | None = Field(None, alias="sessionId")
    chat_id: str | None = Field(None, alias="chatId")
    session_creation_time: datetime | None = Field(None, alias="sessionCreationTime")
    closed_time: datetime | None = Field(None, alias="closedTime")
    queue: str | None = None
    agent_id: str | None = Field(None, alias="agentId")
    agent_name: str | None = Field(None, alias="agentName")
    typification: str | None = None
    conversation_link: str | None = Field(None, alias="conversationLink")
    avg_attending_time: MetricInt = Field(None, alias="avgAttendingTime")
    avg_response_time: MetricInt = Field(None, alias="avgResponseTime")
    open_sessions: MetricInt = Field(None, alias="openSessions")
    closed_sessions: MetricInt = Field(None, alias="closedSessions")
    on_hold: MetricInt = Field(None, alias="onHold")
    op_response_time: MetricInt = Field(None, alias="opResponseTime")
    operator_responses: MetricInt = Field(None, alias="operatorResponses")
    session_transfer_in: MetricInt = Field(None, alias="sessionTransferIn")
    session_transfer_out: MetricInt = Field(None, alias="sessionTransferOut")
    session_transfer_out_no_messages: MetricInt = Field(None, alias="sessionTransferOutNoMessages")
    closed_with_no_messages: MetricInt = Field(None, alias="closedWithNoMessages")
    timeout_no_messages: MetricInt = Field(None, alias="timeoutNoMessages")
    agent_timeout: MetricInt = Field(None, alias="agentTimeout")
    user_timeout: MetricInt = Field(None, alias="userTimeout")
    from_queue_asign_to_op_assigned: MetricInt = Field(None, alias="fromQueueAsignToOpAssigned")
    from_session_start_to_op_first_response: MetricInt = Field(None, alias="fromSessionStartToOpFirstResponse")
    from_queue_asign_to_op_first_response: MetricInt = Field(None, alias="fromQueueAsignToOpFirstResponse")
    from_op_assigned_to_op_first_response: MetricInt = Field(None, alias="fromOpAssignedToOpFirstResponse")
    from_queue_asign_to_session_closed: MetricInt = Field(None, alias="fromQueueAsignToSessionClosed")
    from_op_assignation_to_session_closed: MetricInt = Field(None, alias="fromOpAssignationToSessionClosed")
    session_timeout: MetricInt = Field(None, alias="sessionTimeout")


class AgentMetricsPage(ApiModel):
    items: list[AgentMetricModel] = Field(default_factory=list)
