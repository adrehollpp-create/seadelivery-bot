"""Точка входа для ``uvicorn main:app`` (используется при деплое в webhook-режиме)."""

from seadelivery_bot.webhook_app import app

__all__ = ["app"]
