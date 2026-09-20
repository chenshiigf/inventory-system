from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.categories import router as categories_router
from app.routers.products import router as products_router
from app.routers.warehouses import router as warehouses_router


app = FastAPI(title="Inventory System API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3002"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type"],
)

app.include_router(products_router)
app.include_router(categories_router)
app.include_router(warehouses_router)
