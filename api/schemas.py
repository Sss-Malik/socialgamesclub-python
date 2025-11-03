from pydantic import BaseModel, Field

class CreateAccountRequest(BaseModel):
    backend: str = Field(..., example="juwa")

class RechargeAccountRequest(BaseModel):
    backend: str    = Field(..., example="juwa")
    count: int      = Field(1, example=50)
    amount_to_deduct: int      = Field(1, example=50)
    account_id: str = Field(..., example="abc123")

class WithdrawAccountRequest(BaseModel):
    backend: str = Field(..., example="juwa")
    count: int = Field(1, example=50)
    account_id: str = Field(..., example="abc123")
    redeem_id: int = Field(..., example=123)
    requested_amount: int = Field(..., example=50)

class ReadAccountRequest(BaseModel):
    backend: str = Field(..., example="juwa")
    account_id: str = Field(..., example="abc123")

class RechargeFreeplayRequest(BaseModel):
    backend: str = Field(..., example="juwa")
    account_id: str = Field(..., example="abc123")
    type: str = Field(..., example="signup_freeplay")
    freeplay_id: int = Field(..., example=1)

class ResetPasswordRequest(BaseModel):
    backend: str = Field(..., example="juwa")
    account_id: str = Field(..., example="abc123")
