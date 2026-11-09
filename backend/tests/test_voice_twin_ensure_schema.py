"""voice_twins schema 自愈：老表缺列 → 启动补列 → insert 正常。"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.asyncio
async def test_ensure_voice_twin_schema_adds_missing_columns():
    from app.gateway.voice_twin.ensure_schema import ensure_voice_twin_schema
    from app.gateway.voice_twin.models import VoiceTwinRow

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        # 模拟老部署：只建最小列集（缺 demo_audio_path / source_audio_duration 等后加列）
        await conn.execute(
            text("CREATE TABLE voice_twins (id VARCHAR(32) PRIMARY KEY, user_id VARCHAR(64), voice_name VARCHAR(100), speaker_id VARCHAR(100), model_type INTEGER, language INTEGER, status VARCHAR(20), source_audio_path VARCHAR(255))")
        )
    await ensure_voice_twin_schema(engine)
    async with engine.begin() as conn:
        cols = {r[1] for r in await conn.execute(text("PRAGMA table_info(voice_twins)"))}
    model_cols = {c.name for c in VoiceTwinRow.__table__.columns}
    assert model_cols <= cols, f"仍有缺失列: {model_cols - cols}"
