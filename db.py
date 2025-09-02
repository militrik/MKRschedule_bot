from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncAttrs
from sqlalchemy.orm import DeclarativeBase

_engine = None
_sessionmaker = None

class Base(AsyncAttrs, DeclarativeBase):
    pass

def init_engine(database_url: str):
    global _engine, _sessionmaker
    _engine = create_async_engine(database_url, echo=False, future=True)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)

def get_sessionmaker():
    return _sessionmaker

async def create_all(models_module):
    async with _engine.begin() as conn:
        await conn.run_sync(models_module.Base.metadata.create_all)
