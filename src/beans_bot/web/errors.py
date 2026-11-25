"""
Exceptions -> HTTP statuses. Every error body is `{"detail": "<message>"}`.

- UserFacingError, AmountError and invalid requests: 400. The messages are in the bot's voice; shown as they are.
- Unknown guild or row: 404. Domain conflicts (not enough coins, already jailed, retrain running): 409.
- The bot isn't connected, or the cog an action needs isn't loaded: 503.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from beans_bot.currency import AmountError
from beans_bot.services.classifier import RetrainInProgress
from beans_bot.services.coins import InsufficientFunds
from beans_bot.services.errors import UserFacingError
from beans_bot.services.jail import AlreadyJailed


def bad_request(detail: str) -> HTTPException:
    return HTTPException(400, detail)


def not_found(detail: str) -> HTTPException:
    return HTTPException(404, detail)


def conflict(detail: str) -> HTTPException:
    return HTTPException(409, detail)


def bad_gateway(detail: str) -> HTTPException:
    return HTTPException(502, detail)


def unavailable(detail: str) -> HTTPException:
    return HTTPException(503, detail)


NOT_CONNECTED = "The bot isn't connected to Discord right now, so it can't do that."


def _message(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status)


def _validation_text(errors) -> str:
    parts = []
    for error in errors:
        where = ".".join(str(p) for p in error.get("loc", ()) if p not in ("body", "query", "path"))
        parts.append(f"{where}: {error.get('msg')}" if where else str(error.get("msg")))
    return "; ".join(parts) or "Invalid request."


def install(app: FastAPI) -> None:
    async def user_facing(request: Request, exc: Exception) -> JSONResponse:
        return _message(400, str(exc))

    async def invalid_request(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, (RequestValidationError, ValidationError))
        return _message(400, _validation_text(exc.errors()))

    async def insufficient(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, InsufficientFunds)
        currency = request.app.state.ctx.services.currency
        return _message(409, f"Not enough coins: the balance is {currency.format(exc.balance)}, and {currency.format(exc.needed)} is needed.")

    async def retraining(request: Request, exc: Exception) -> JSONResponse:
        return _message(409, "A retrain is already running. Wait for it to finish.")

    async def jailed(request: Request, exc: Exception) -> JSONResponse:
        return _message(409, "That member is already in jail.")

    app.add_exception_handler(UserFacingError, user_facing)
    app.add_exception_handler(AmountError, user_facing)
    app.add_exception_handler(RequestValidationError, invalid_request)
    app.add_exception_handler(ValidationError, invalid_request)
    app.add_exception_handler(InsufficientFunds, insufficient)
    app.add_exception_handler(RetrainInProgress, retraining)
    app.add_exception_handler(AlreadyJailed, jailed)
