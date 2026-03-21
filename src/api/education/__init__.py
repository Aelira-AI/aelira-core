"""Education API routes package."""

from fastapi import APIRouter

from .scan_routes import router as scan_router
from .scan_history_routes import router as scan_history_router
from .image_routes import router as image_router
from .web_scan_routes import router as web_scan_router
from .multimedia_routes import router as multimedia_router
from .remediation_routes import router as remediation_router
from .compliance_routes import router as compliance_router
from .gamification_routes import router as gamification_router
from .accessibility_routes import router as accessibility_router

router = APIRouter(prefix="/education", tags=["education"])
router.include_router(scan_router)
router.include_router(scan_history_router)
router.include_router(image_router)
router.include_router(web_scan_router)
router.include_router(multimedia_router)
router.include_router(remediation_router)
router.include_router(compliance_router)
router.include_router(gamification_router)
router.include_router(accessibility_router)
