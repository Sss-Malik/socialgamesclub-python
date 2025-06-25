from pydantic import BaseModel, Field

class CreateAccountRequest(BaseModel):
    backend: str = Field(..., example="juwa")
    count: int   = Field(1, example=50)

class RechargeAccountRequest(BaseModel):
    backend: str    = Field(..., example="juwa")
    count: int      = Field(1, example=50)
    account_id: str = Field(..., example="abc123")

class WithdrawAccountRequest(BaseModel):
    backend: str = Field(..., example="juwa")
    count: int = Field(1, example=50)
    account_id: str = Field(..., example="abc123")

class ReadAccountRequest(BaseModel):
    backend: str = Field(..., example="juwa")
    account_id: str = Field(..., example="abc123")