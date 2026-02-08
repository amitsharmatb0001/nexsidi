"""
Browser Pool - Manage Playwright Browsers
"""

from playwright.async_api import async_playwright
import asyncio
import logging

logger = logging.getLogger("browser_pool")


class BrowserPool:
    """Pool of reusable browser instances with project isolation"""
    
    def __init__(self, max_browsers=3):
        self.max_browsers = max_browsers
        self.available = asyncio.Queue(maxsize=max_browsers)
        self.lock = asyncio.Lock()
        self.initialized = False
        # Track which project is using which browser
        self.project_assignments: Dict[str, List[Tuple]] = {}
    
    async def initialize(self):
        """Initialize browser pool"""
        async with self.lock:
            if self.initialized:
                return
            
            for i in range(self.max_browsers):
                playwright = await async_playwright().start()
                browser = await playwright.chromium.launch(
                    headless=True,
                    args=['--no-sandbox', '--disable-dev-shm-usage']
                )
                await self.available.put((playwright, browser))
                logger.info(f"Browser {i+1}/{self.max_browsers} initialized")
            
            self.initialized = True
    
    async def acquire(self, project_id: str):
        """
        Get browser from pool (blocks if none available).
        Tracks assignment to project_id.
        """
        if not self.initialized:
            await self.initialize()
        
        resource = await self.available.get()
        
        # Track assignment
        if project_id not in self.project_assignments:
            self.project_assignments[project_id] = []
        self.project_assignments[project_id].append(resource)
        
        logger.info(f"Browser acquired for project {project_id}")
        return resource
    
    async def release(self, project_id: str, playwright, browser):
        """Return browser to pool and clear assignment"""
        resource = (playwright, browser)
        
        if project_id in self.project_assignments:
            if resource in self.project_assignments[project_id]:
                self.project_assignments[project_id].remove(resource)
                if not self.project_assignments[project_id]:
                    del self.project_assignments[project_id]
        
        await self.available.put(resource)
        logger.info(f"Browser released for project {project_id}")
    
    async def cleanup_project(self, project_id: str):
        """Forcefully release all browsers assigned to a project"""
        if project_id in self.project_assignments:
            resources = self.project_assignments[project_id][:]
            for playwright, browser in resources:
                await self.release(project_id, playwright, browser)
            logger.info(f"Cleaned up all browsers for project {project_id}")

    async def cleanup(self):
        """Close all browsers and stop playwright"""
        async with self.lock:
            while not self.available.empty():
                playwright, browser = await self.available.get()
                await browser.close()
                await playwright.stop()
            self.project_assignments.clear()
            self.initialized = False
            logger.info("Browser pool cleaned up and closed")


# Global pool
browser_pool = BrowserPool(max_browsers=3)
