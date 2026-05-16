"""Allow running with: python3 -m src"""
import uvicorn

uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
