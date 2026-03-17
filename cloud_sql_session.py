from __future__ import annotations
import asyncio
import os
from typing import Any
from google.cloud.sql.connector import Connector
import sqlalchemy
from sqlalchemy.ext.asyncio import create_async_engine
from google.adk.sessions.database_session_service import DatabaseSessionService

class CloudSQLSessionService(DatabaseSessionService):
    """
    A persistent session service using Google Cloud SQL (PostgreSQL).
    Extends ADK's DatabaseSessionService to handle Cloud SQL IAM authentication
    and secure tunnel connections via the Cloud SQL Python Connector.
    """

    def __init__(
        self,
        instance_connection_name: str,
        db_user: str = "postgres",
        db_name: str = "postgres",
        db_password: str | None = None,
        **kwargs: Any
    ):
        """
        Initializes the Cloud SQL session service.
        """
        self.instance_connection_name = instance_connection_name
        self.db_user = db_user
        self.db_name = db_name
        self.db_password = db_password

        # Build the async engine using the connector's async_creator function.
        # We use a dummy URL that specifies the driver, but the creator will handle the actual connection.
        db_url = "postgresql+asyncpg://postgres:pass@localhost/postgres"

        # Initialize connector once bound to the running loop to avoid 2-3s IAM handshake per connection
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        self._connector = Connector(loop=loop)

        async def get_conn():
            conn = await self._connector.connect_async(
                self.instance_connection_name,
                "asyncpg",
                user=self.db_user,
                password=self.db_password,
                db=self.db_name,
            )
            return conn

        # ADK DatabaseSessionService calls create_async_engine(db_url, **kwargs).
        # We pass async_creator in kwargs which is passed to create_async_engine.
        super().__init__(db_url=db_url, async_creator=get_conn, **kwargs)

    async def close(self) -> None:
        """Cleanup session service resources."""
        await super().close()
        # Close the background connector process
        await self._connector.close_async()

    @classmethod
    async def create(
        cls,
        instance_connection_name: str,
        db_user: str = "postgres",
        db_name: str = "postgres",
        db_password: str | None = None,
        **kwargs: Any
    ) -> CloudSQLSessionService:
        """Factory method to create and initialize the service."""
        instance = cls(
            instance_connection_name=instance_connection_name,
            db_user=db_user,
            db_name=db_name,
            db_password=db_password,
            **kwargs
        )
        return instance
