from __future__ import annotations

import os

import uvicorn


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8077")),
        reload=os.getenv("RELOAD", "").lower() in {"1", "true", "yes"},
    )
