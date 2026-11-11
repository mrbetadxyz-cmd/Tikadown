from aiogram import Router
from handlers.start import router as start_router
from handlers.downloader import router as download_router
from handlers.admin import router as admin_router

# Core router to group all handlers
router = Router()
router.include_router(start_router)
router.include_router(admin_router)
router.include_router(download_router)
