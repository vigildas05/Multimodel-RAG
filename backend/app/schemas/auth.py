from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.auth.passwords import PASSWORD_MAX_LENGTH


class CredentialsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    # Policy (minimum length etc.) is checked in the auth service; this only bounds input size.
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class UserOut(BaseModel):
    user_id: str
    email: str
    role: str
    workspace_id: str
    is_demo: bool


class SessionResponse(BaseModel):
    user: UserOut
    csrf_token: str


class CsrfResponse(BaseModel):
    csrf_token: str


class AdminUserOut(BaseModel):
    user_id: str
    email: str
    role: str
    is_active: bool
    is_demo: bool
    created_at: str
    last_login_at: str | None


class AdminUserListResponse(BaseModel):
    users: list[AdminUserOut]
