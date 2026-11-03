from fastapi import FastAPI

app = FastAPI(title="conomi")


@app.get("/health")
def health() -> dict[str, str]:
    return {"Status": "UP"}
