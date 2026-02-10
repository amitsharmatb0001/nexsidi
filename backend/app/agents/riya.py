"""
RIYA - MOBILE APP DEVELOPER (CROSS-PLATFORM)
============================================
Location: app/agents/riya.py

Purpose: Generate mobile applications using cross-platform frameworks
Platforms: Flutter (iOS + Android), PWA (Universal), React Native

Deployment: GCP only (Cloud Build, Firebase)
Build capability: Android (Linux), iOS (when Mac available)
"""

import json
import logging
import asyncio
from typing import Dict, Any, List, Optional
from app.services.ai_router import ai_router, TaskComplexity
from app.services.prompt_engine import prompt_engine
from app.agents.mixins import (
    MistakeMemoryMixin, 
    SearchCapableMixin, 
    PermanentMemoryMixin,
    ContextManagementMixin,
    DecisionLedgerMixin,
    ProgressMixin
)


class Riya(MistakeMemoryMixin, PermanentMemoryMixin, ContextManagementMixin, DecisionLedgerMixin, SearchCapableMixin, ProgressMixin):
    """
    Mobile App Developer Agent
    
    Capabilities:
    - Flutter (cross-platform: iOS + Android + Web)
    - Progressive Web Apps (universal)
    - React Native (cross-platform)
    
    GCP-optimized:
    - Android builds on Cloud Build (Linux)
    - iOS code generated (build when Mac available)
    - Firebase integration
    - PWA deployment
    """
    
    def __init__(self, project_id: str, workspace: Dict[str, str]):
        """Initialize Riya for mobile development"""
        super().__init__()
        
        self.project_id = project_id
        self.agent_name = "riya"
        self.ai_router = ai_router
        self.logger = logging.getLogger(f"agent.riya.{project_id}")
        self.workspace = workspace
        
        # Statistics
        self.apps_generated = 0
        self.total_cost = 0.0
        
        # Platform detection
        self.has_macos = False  # Set to True when Mac available
    
    async def execute(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate mobile application
        
        Args:
            input_data: {
                "platforms": ["ios", "android", "web"],  # Target platforms
                "framework": "flutter|pwa|react_native",  # Or auto-detect
                "requirements": {...},
                "design_system": {...},  # From Vanya
                "api_base_url": "https://..."  # Backend API
            }
        
        Returns:
            {
                "status": "success",
                "framework": "flutter",
                "platforms_supported": ["android", "ios"],
                "platforms_built": ["android"],  # iOS when Mac available
                "code": {...},
                "build_artifacts": {...},
                "deployment": {...}
            }
        """
        try:
            self.logger.info("📱 Starting mobile app generation...")
            
            platforms = input_data.get("platforms", ["android"])
            framework = input_data.get("framework")
            requirements = input_data.get("requirements", {})
            
            # Auto-select framework if not specified
            if not framework:
                framework = self._select_framework(platforms, requirements)
            
            self.logger.info(f"🎯 Framework selected: {framework}")
            
            # Generate based on framework
            await self._send_progress("mobile_generation", 20, f"Generating {framework} project structure...")
            if framework == "flutter":
                result = await self._generate_flutter_app(input_data)
            elif framework == "pwa":
                result = await self._generate_pwa(input_data)
            elif framework == "react_native":
                result = await self._generate_react_native_app(input_data)
            else:
                raise ValueError(f"Unknown framework: {framework}")
                
            await self._send_progress("mobile_generation", 100, f"Mobile app ({framework}) generation complete.")
            
            self.apps_generated += 1
            result["cost"] = self.total_cost
            
            self.logger.info(f"✅ Mobile app generated: {framework}")
            return result
            
        except Exception as e:
            self.logger.error(f"❌ Mobile app generation failed: {e}")
            await self.record_failure(
                task_type="mobile_execution",
                error=str(e),
                context={"input_data": input_data}
            )
            raise
    
    def _select_framework(
        self,
        platforms: List[str],
        requirements: Dict[str, Any]
    ) -> str:
        """
        Auto-select best framework based on requirements
        
        Decision tree:
        - Need native performance? → Flutter
        - Need web + mobile? → PWA
        - JavaScript team? → React Native
        - Default → Flutter (Google's recommendation)
        """
        # If iOS + Android + Web → PWA is easiest
        if len(platforms) >= 3:
            return "pwa"
        
        # If performance critical → Flutter
        if requirements.get("performance") == "high":
            return "flutter"
        
        # If JavaScript preference → React Native
        if requirements.get("language_preference") == "javascript":
            return "react_native"
        
        # Default: Flutter (best cross-platform)
        return "flutter"
    
    async def _generate_flutter_app(
        self,
        input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Generate Flutter application (cross-platform)
        
        Output: ONE codebase for iOS + Android + Web
        Build: Android on Linux, iOS needs Mac
        """
        self.logger.info("🎨 Generating Flutter app...")
        
        requirements = input_data.get("requirements", {})
        design_system = input_data.get("design_system", {})
        api_base_url = input_data.get("api_base_url", "")
        
        # Generate Flutter project structure
        flutter_project = await self._create_flutter_project(
            requirements,
            design_system,
            api_base_url
        )
        
        # Save code to workspace
        self._save_flutter_code(flutter_project)
        
        # Build artifacts (Android on Linux, iOS needs Mac)
        build_result = await self._build_flutter_artifacts(flutter_project)
        
        return {
            "status": "success",
            "framework": "flutter",
            "platforms_supported": ["ios", "android", "web"],
            "platforms_built": build_result["platforms_built"],
            "code": flutter_project,
            "build_artifacts": build_result,
            "deployment": {
                "android": build_result.get("android_apk_url"),
                "ios": "Code ready - build when Mac available",
                "web": build_result.get("web_url")
            }
        }
    
    async def _create_flutter_project(
        self,
        requirements: Dict[str, Any],
        design_system: Dict[str, Any],
        api_base_url: str
    ) -> Dict[str, Any]:
        """
        Generate complete Flutter project structure
        
        AI generates:
        - pubspec.yaml (dependencies)
        - main.dart (entry point)
        - screens/* (UI screens)
        - services/* (API integration)
        - models/* (data models)
        - widgets/* (reusable components)
        """
        prompt = prompt_engine.get_prompt(
            "riya",
            "flutter_app",
            context={
                "requirements": json.dumps(requirements, indent=2),
                "design_system": json.dumps(design_system, indent=2),
                "api_base_url": api_base_url
            }
        )

        # Check past mistakes for flutter generation
        past_mistakes = await self.check_past_mistakes(
            task_type="flutter_generation",
            context={"project_id": self.project_id}
        )
        
        if past_mistakes:
            prompt = self.incorporate_past_learnings(past_mistakes, prompt)
            self.logger.info(f"📚 Incorporated {len(past_mistakes)} past learnings for flutter")
        
        response = await self.ai_router.generate(
            messages=[{"role": "user", "content": prompt}],
            task_type="mobile_development",
            complexity=TaskComplexity.COMPLEX,
            max_tokens=8000
        )
        
        self.total_cost += response.cost_estimate
        
        try:
            flutter_project = json.loads(response.content)
            return flutter_project
        except json.JSONDecodeError:
            # Fallback: create basic structure
            return self._default_flutter_project()
    
    async def _build_flutter_artifacts(
        self,
        flutter_project: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Build Flutter artifacts
        
        Linux (GCP):
        - ✅ Android APK
        - ✅ Web build
        - ❌ iOS IPA (needs Mac)
        
        Returns URLs and installation instructions
        """
        platforms_built = []
        
        # Build Android APK (works on Linux/GCP)
        android_result = await self._build_android_apk(flutter_project)
        if android_result["status"] == "success":
            platforms_built.append("android")
        
        # Build Web (works anywhere)
        web_result = await self._build_flutter_web(flutter_project)
        if web_result["status"] == "success":
            platforms_built.append("web")
        
        # iOS: Code is ready, but can't build without Mac
        ios_status = "code_ready_needs_mac"
        
        return {
            "platforms_built": platforms_built,
            "android_apk_url": android_result.get("apk_url"),
            "android_qr_code": android_result.get("qr_code"),
            "web_url": web_result.get("url"),
            "ios_status": ios_status,
            "ios_instructions": "Run 'flutter build ios' on Mac to build IPA"
        }
    
    async def _build_android_apk(
        self,
        flutter_project: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Build Android APK using GCP Cloud Build
        
        Process:
        1. Upload Flutter code to Cloud Storage
        2. Trigger Cloud Build with Flutter builder
        3. Build APK
        4. Upload to Firebase App Distribution
        5. Return download URL and QR code
        """
        self.logger.info("🤖 Building Android APK...")
        
        # For now, return mock result
        # TODO: Integrate with actual GCP Cloud Build
        return {
            "status": "success",
            "apk_url": f"https://storage.googleapis.com/nexsidi-builds/{self.project_id}/app-release.apk",
            "qr_code": f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=apk_url",
            "firebase_distribution_link": f"https://appdistribution.firebase.dev/i/{self.project_id}"
        }
    
    async def _build_flutter_web(
        self,
        flutter_project: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Build Flutter for web deployment
        
        Process:
        1. Run flutter build web
        2. Deploy to Firebase Hosting
        3. Return URL
        """
        self.logger.info("🌐 Building Flutter Web...")
        
        # Mock result
        return {
            "status": "success",
            "url": f"https://{self.project_id}.web.app"
        }
    
    async def _generate_pwa(
        self,
        input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Generate Progressive Web App
        
        Perfect for demo:
        - Works on iOS + Android + Desktop
        - Install in 30 seconds
        - No app store needed
        - Full mobile features
        """
        self.logger.info("🌐 Generating PWA...")
        
        requirements = input_data.get("requirements", {})
        design_system = input_data.get("design_system", {})
        api_base_url = input_data.get("api_base_url", "")
        
        # Generate web app first (React/Vue)
        web_app = await self._create_web_app(
            requirements,
            design_system,
            api_base_url
        )
        
        # Add PWA features
        pwa_app = await self._add_pwa_features(web_app)
        
        # Deploy to Firebase Hosting
        deployment = await self._deploy_pwa(pwa_app)
        
        return {
            "status": "success",
            "framework": "pwa",
            "platforms_supported": ["ios", "android", "web", "desktop"],
            "code": pwa_app,
            "deployment": {
                "url": deployment["url"],
                "qr_code": deployment["qr_code"],
                "install_instructions": {
                    "ios": "Open in Safari → Share → Add to Home Screen",
                    "android": "Open in Chrome → Menu → Add to Home Screen",
                    "desktop": "Browser will show install prompt"
                }
            }
        }
    
    async def _create_web_app(
        self,
        requirements: Dict[str, Any],
        design_system: Dict[str, Any],
        api_base_url: str
    ) -> Dict[str, Any]:
        """Generate React/Vue web app"""
        # Use Aanya's architecture for web app
        # Then convert to PWA
        prompt = prompt_engine.get_prompt(
            "riya",
            "web_app_for_pwa",
            context={
                "requirements": json.dumps(requirements, indent=2),
                "design_system": json.dumps(design_system, indent=2),
                "api_base_url": api_base_url
            }
        )

        # Check past mistakes for PWA generation
        past_mistakes = await self.check_past_mistakes(
            task_type="pwa_generation",
            context={"project_id": self.project_id}
        )
        
        if past_mistakes:
            prompt = self.incorporate_past_learnings(past_mistakes, prompt)
            self.logger.info(f"📚 Incorporated {len(past_mistakes)} past learnings for PWA")
        
        response = await self.ai_router.generate(
            messages=[{"role": "user", "content": prompt}],
            task_type="frontend_development",
            complexity=TaskComplexity.COMPLEX,
            max_tokens=6000
        )
        
        self.total_cost += response.cost_estimate
        
        try:
            return json.loads(response.content)
        except json.JSONDecodeError:
            return self._default_web_app()
    
    async def _add_pwa_features(
        self,
        web_app: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Convert web app to PWA
        
        Adds:
        - manifest.json (app metadata)
        - service-worker.js (offline support)
        - Icons (various sizes)
        - Meta tags for mobile
        """
        pwa_app = web_app.copy()
        
        # Add manifest.json
        pwa_app["manifest.json"] = {
            "name": web_app.get("name", "Mobile App"),
            "short_name": web_app.get("short_name", "App"),
            "start_url": "/",
            "display": "standalone",
            "background_color": "#ffffff",
            "theme_color": "#000000",
            "icons": [
                {
                    "src": "/icon-192.png",
                    "sizes": "192x192",
                    "type": "image/png"
                },
                {
                    "src": "/icon-512.png",
                    "sizes": "512x512",
                    "type": "image/png"
                }
            ]
        }
        
        # Add service worker
        pwa_app["service-worker.js"] = """
// Service Worker for offline support
const CACHE_NAME = 'pwa-cache-v1';

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll([
        '/',
        '/index.html',
        '/static/css/main.css',
        '/static/js/main.js'
      ]);
    })
  );
});

self.addEventListener('fetch', (event) => {
  event.respondWith(
    caches.match(event.request).then((response) => {
      return response || fetch(event.request);
    })
  );
});
"""
        
        return pwa_app
    
    async def _deploy_pwa(
        self,
        pwa_app: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Deploy PWA to Firebase Hosting
        
        Returns:
        - URL
        - QR code for mobile access
        """
        # Mock deployment
        url = f"https://{self.project_id}.web.app"
        qr_code = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={url}"
        
        return {
            "url": url,
            "qr_code": qr_code
        }
    
    async def _generate_react_native_app(
        self,
        input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Generate React Native application
        
        Similar to Flutter but JavaScript-based
        """
        self.logger.info("⚛️ Generating React Native app...")
        
        # Similar process to Flutter
        # For brevity, using simplified version
        
        return {
            "status": "success",
            "framework": "react_native",
            "platforms_supported": ["ios", "android"],
            "platforms_built": ["android"],  # iOS needs Mac
            "note": "React Native implementation - similar to Flutter"
        }
    
    def _default_flutter_project(self) -> Dict[str, Any]:
        """Fallback Flutter project structure"""
        return {
            "pubspec.yaml": """
name: mobile_app
description: Generated by NexSidi
version: 1.0.0

environment:
  sdk: '>=3.0.0 <4.0.0'

dependencies:
  flutter:
    sdk: flutter
  http: ^1.1.0
  provider: ^6.1.0
""",
            "lib/main.dart": """
import 'package:flutter/material.dart';

void main() => runApp(MyApp());

class MyApp extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Mobile App',
      home: Scaffold(
        appBar: AppBar(title: Text('Home')),
        body: Center(child: Text('Generated by NexSidi')),
      ),
    );
  }
}
"""
        }
    
    def _default_web_app(self) -> Dict[str, Any]:
        """Fallback web app structure"""
        return {
            "name": "Web App",
            "framework": "react",
            "files": {}
        }
    
    def _save_flutter_code(self, flutter_project: Dict[str, Any]):
        """Save Flutter code to workspace"""
        import os
        
        mobile_dir = os.path.join(self.workspace['code_dir'], 'mobile')
        os.makedirs(mobile_dir, exist_ok=True)
        
        # Save files
        for filename, content in flutter_project.items():
            filepath = os.path.join(mobile_dir, filename)
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            
            with open(filepath, 'w') as f:
                f.write(content if isinstance(content, str) else json.dumps(content, indent=2))
        
        self.logger.info(f"📁 Flutter code saved to: {mobile_dir}")
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get generation statistics"""
        return {
            "apps_generated": self.apps_generated,
            "total_cost": self.total_cost
        }


if __name__ == "__main__":
    import asyncio
    
    async def test():
        riya = Riya(
            project_id="test-mobile-001",
            workspace={"code_dir": "/tmp/test"}
        )
        
        # Test Flutter generation
        result = await riya.execute({
            "platforms": ["ios", "android"],
            "framework": "flutter",
            "requirements": {
                "app_type": "restaurant_booking",
                "features": ["table_booking", "menu_view", "reviews"]
            },
            "design_system": {
                "colors": {"primary": "#FF5722"}
            },
            "api_base_url": "https://api.example.com"
        })
        
        print(json.dumps(result, indent=2))
        print(f"\nStatistics: {riya.get_statistics()}")
    
    asyncio.run(test())
