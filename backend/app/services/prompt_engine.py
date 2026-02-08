import logging
from typing import Dict, Any, Optional
import re

# File: app/services/prompt_engine.py

class PromptEngine:
    """
    Manage prompts for all agents with versioning and context injection.
    Acting as a centralized Prompt CMS.
    """
    
    def __init__(self):
        self.logger = logging.getLogger("prompt_engine")
        self.prompts_db = {}  # Format: {agent_name: {task_type: {version: template}}}
        self.load_default_prompts()
    
    def get_prompt(self, agent_name: str, task_type: str, 
                   context: Dict[str, Any] = None, version: str = "latest") -> str:
        """
        Get prompt with context injection.
        
        Args:
            agent_name: Name of the agent (e.g., "saanvi")
            task_type: Specific task (e.g., "requirements_analysis")
            context: Dictionary of values to inject into template
            version: Version string, version ID, or "latest"
            
        Returns:
            Formatted prompt string
        """
        if not context:
            context = {}
            
        from app.services.prompt_versioning import prompt_versioning
        
        template = None
        if version == "latest":
            template = prompt_versioning.get_active_prompt(agent_name, task_type)
        else:
            # Try to get by version ID first
            template = prompt_versioning.get_prompt_by_version(agent_name, task_type, version)
            
        # Fallback to legacy dictionary if not in versioning or for compatibility during migration
        if not template:
            agent_prompts = self.prompts_db.get(agent_name, {})
            task_prompts = agent_prompts.get(task_type, {})
            
            if not task_prompts:
                self.logger.warning(f"⚠️ Prompt not found: {agent_name}/{task_type}")
                return f"Error: Prompt template not found for {agent_name}/{task_type}"
                
            if version == "latest":
                version_key = sorted(task_prompts.keys())[-1]
                template = task_prompts.get(version_key)
            else:
                template = task_prompts.get(version)

        if not template:
            self.logger.warning(f"⚠️ Prompt version not found: {agent_name}/{task_type} v{version}")
            return f"Error: Prompt version {version} not found"
            
        # Inject context
        return self.inject_context(template, context)
    
    def inject_context(self, prompt_template: str, context: Dict[str, Any]) -> str:
        """
        Inject context variables into prompt template.
        Supports {variable} syntax.
        Gracefully handles missing keys by leaving them as placeholders or empty.
        """
        try:
            # Use safe formatting that doesn't crash on missing keys
            # We use a custom approach to handle missing keys gracefully
            
            # 1. Identify all {placeholders}
            placeholders = re.findall(r'\{(\w+)\}', prompt_template)
            
            # 2. Prepare safe context
            safe_context = {}
            for p in placeholders:
                val = context.get(p, f"[{p} NOT PROVIDED]")
                # Convert complex objects to string if needed
                if isinstance(val, (dict, list)):
                    import json
                    val = json.dumps(val, indent=2)
                safe_context[p] = str(val)
                
            # 3. Format
            return prompt_template.format(**safe_context)
            
        except Exception as e:
            self.logger.error(f"❌ formatting failed: {e}")
            return prompt_template
    
    def register_prompt(self, agent_name: str, task_type: str, 
                       prompt_template: str, version: str = "1.0.0"):
        """Register new prompt version"""
        if agent_name not in self.prompts_db:
            self.prompts_db[agent_name] = {}
        
        if task_type not in self.prompts_db[agent_name]:
            self.prompts_db[agent_name][task_type] = {}
            
        self.prompts_db[agent_name][task_type][version] = prompt_template
        
        # Also register in PromptVersioning
        from app.services.prompt_versioning import prompt_versioning
        version_id = prompt_versioning.register_prompt(
            agent_name=agent_name,
            prompt_key=task_type,
            prompt_text=prompt_template,
            metadata={"version_name": version}
        )
        
        self.logger.info(f"📝 Registered prompt: {agent_name}/{task_type} v{version} (ID: {version_id})")
    
    def load_default_prompts(self):
        """Load default prompts for all agents"""
        
        # =====================================================================
        # TILOTMA PROMPTS
        # =====================================================================
        
        self.register_prompt("tilotma", "requirements_gathering", """
You are Tilotma, the Chief Project Manager at NexSidi.
Your goal is to understand what the user wants to build.

USER MESSAGE: "{user_message}"
CURRENT CONTEXT: {context_summary}

TASK:
1. Analyze the user's request.
2. If the request is vague, ask clarifying questions.
3. If the request is clear, acknowledge it and propose next steps.
4. Be professional but friendly.

Respond in natural language.
""")

        self.register_prompt("tilotma", "readiness_check", """
Analyze if we have enough information to generate a full specification.

PROJECT DESCRIPTION: "{project_description}"
CONVERSATION HISTORY: {conversation_history}

CHECKLIST:
1. Core features identified?
2. Target audience known?
3. Platform (Web/Mobile) decided?
4. Key technical constraints known?

OUTPUT JSON:
{
    "is_ready": true/false,
    "confidence": 0.0-1.0,
    "missing_info": ["list", "of", "missing", "items"],
    "reasoning": "explanation"
}
""")
        
        self.register_prompt("tilotma", "validation_check", """
Validate this output from {agent_name} for task {task_type}.

OUTPUT TO VALIDATE:
{agent_output}

REQUIREMENTS:
{requirements}

TASK:
Check for:
1. Completeness
2. Consistency with requirements
3. Quality issues

OUTPUT JSON:
{
    "is_valid": true/false,
    "issues": ["list", "of", "issues"],
    "score": 1-10
}
""")

        # =====================================================================
        # SAANVI PROMPTS
        # =====================================================================

        self.register_prompt("saanvi", "requirements_analysis", """
You are Saanvi, the Requirements Analyst for NexSidi.

PROJECT CONTEXT:
- User Description: {user_requirements}
- Detected Features: {detected_features}
- Target Audience: {target_audience}

YOUR TASK:
Analyze these requirements and provide:
1. Detailed feature breakdown
2. Technical requirements
3. Complexity score (1-10)
4. Estimated timeline

OUTPUT FORMAT:
Return JSON with these exact keys:
{
    "functional_requirements": [{"name": "Auth", "description": "...", "priority": "High"}],
    "non_functional_requirements": ["Security", "Scalability"],
    "user_stories": ["As a user I want..."],
    "risk_assessment": ["Risk 1..."]
}
""")

        self.register_prompt("saanvi", "complexity_estimation", """
Calculate the complexity score (1-10) for this project.

REQUIREMENTS:
{requirements}

SCORING GUIDE:
1-3: Simple landing pages, basic CRUD
4-6: E-commerce, Standard SaaS, Social features
7-9: Real-time, AI integration, High scale, Complex logic
10: Enterprise OS, Core banking, Custom blockchain

OUTPUT JSON:
{
    "score": 5,
    "reasoning": "...",
    "price_estimate_inr": 50000
}
""")

        self.register_prompt("saanvi", "tech_stack_recommendation", """
Recommend the best technology stack for this project.

REQUIREMENTS:
{requirements}
PROJECT TYPE: {project_type}

Consider: Performance, Scalability, Dev Speed, Maintenance.

OUTPUT JSON:
{
    "frontend": "React/Next.js/etc",
    "backend": "FastAPI/Django/Node",
    "database": "PostgreSQL/MongoDB",
    "infrastructure": "AWS/Vercel",
    "libraries": ["list", "of", "key", "libs"]
}
""")

        # =====================================================================
        # SHUBHAM PROMPTS
        # =====================================================================

        self.register_prompt("shubham", "backend_generation", """
Generate production-ready Python/FastAPI code for: {file_path}

DESCRIPTION: {file_description}
ARCHITECTURE: {architecture}
DEPENDENCIES: {dependencies}
SPECIFIC INSTRUCTIONS:
{specific_instructions}

GUIDELINES:
- Use FastAPI best practices
- Add type hints and docstrings
- Error handling is mandatory
- Use Async/Await

OUTPUT:
Return ONLY the python code.
""")

        self.register_prompt("shubham", "api_design", """
Design the REST API structure for this project.

REQUIREMENTS:
{requirements}

OUTPUT JSON:
{
    "endpoints": [
        {
            "path": "/api/v1/users",
            "method": "POST",
            "description": "Create user",
            "request_model": "UserCreate",
            "response_model": "UserResponse"
        }
    ],
    "models": ["User", "Product"]
}
""")

        self.register_prompt("shubham", "database_schema", """
Design the Database Schema (PostgreSQL).

REQUIREMENTS:
{requirements}

OUTPUT JSON:
{
    "tables": [
        {
            "name": "users",
            "columns": [
                {"name": "id", "type": "UUID", "primary_key": true},
                {"name": "email", "type": "VARCHAR(255)", "unique": true}
            ],
            "relationships": []
        }
    ]
}
""")

        # =====================================================================
        # AANYA PROMPTS
        # =====================================================================

        self.register_prompt("aanya", "system_prompt", """
You are Aanya, a senior frontend developer specializing in React and TypeScript.

YOUR EXPERTISE:
- React 18+ (hooks, context, components)
- TypeScript (strict types, interfaces)
- React Router v6
- Tailwind CSS
- Fetch API
- Responsive design
- Accessibility

YOUR TASK:
Implement frontend architecture designed by Saanvi.

WHAT YOU GENERATE (Frontend Only):
1. React Components - Functional with TypeScript
2. Pages - Full page components with routing
3. API Integration - Fetch calls to backend
4. Styling - Tailwind utility classes
5. Configuration - package.json, tsconfig.json

CRITICAL - OUTPUT FORMAT:
To avoid JSON encoding issues, assume standard JSON string behavior with proper escaping.

{
    "file_path": "frontend/src/components/MenuItem.tsx",
    "file_content": "import React from 'react';\\n\\nconst MenuItem = () => {...}",
    "file_type": "typescript-react",
    "description": "Menu item component"
}

IMPORTANT: Return actual code in file_content, not base64.
Use proper JSON escaping for quotes and newlines.
""")

        self.register_prompt("aanya", "frontend_generation", """
Generate React/Next.js component code for: {component_name}

DESCRIPTION: {description}
DESIGN SYSTEM: {design_system}
API CONTRACT: {api_contract}

GUIDELINES:
- Functional components with Hooks
- TypeScript usage
- Tailwind CSS for styling
- Responsive design
- Accessibility

CRITICAL - OUTPUT FORMAT:
To avoid JSON encoding issues, assume standard JSON string behavior.

OUTPUT JSON:
{
    "file_path": "{component_name}",
    "file_content": "import React... code here",
    "file_type": "typescript-react",
    "description": "Brief description"
}

IMPORTANT: Return actual code in file_content, not base64.
Use proper JSON escaping for quotes and newlines.
""")

        self.register_prompt("aanya", "responsive_design", """
Generate responsive CSS (Tailwind classes) for this layout structure.

LAYOUT: {layout_description}
BREAKPOINTS: Mobile, Tablet, Desktop

OUTPUT JSON:
{
    "container_classes": "w-full md:w-3/4 lg:w-1/2...",
    "grid_classes": "grid grid-cols-1 md:grid-cols-2...",
    "font_sizes": "text-sm md:text-base..."
}
""")
        
        self.register_prompt("aanya", "component_structure", """
Plan the React Component Hierarchy.

FEATURES: {features}

OUTPUT JSON:
{
    "components": [
        {"name": "Navbar", "type": "molecule", "props": ["user"]},
        {"name": "DashboardLayout", "type": "template", "children": ["Sidebar", "Content"]}
    ]
}
""")

        # =====================================================================
        # KARAN PROMPTS
        # =====================================================================

        self.register_prompt("karan", "security_review", """
You are KARAN, an adversarial security vulnerability detection agent.

YOUR ONLY GOAL: Find AS MANY security vulnerabilities as possible in this code.

REWARD SYSTEM:
- You get +2 points for EVERY CRITICAL vulnerability
- You get +1 point for HIGH vulnerabilities
- Think like a HACKER - how would you exploit this code?

CODE TO ANALYZE:
```{file_type}
{code_content}
```

HUNT AGGRESSIVELY FOR:
1. SQL INJECTION (CWE-89) [CRITICAL]
2. CROSS-SITE SCRIPTING - XSS (CWE-79) [CRITICAL]
3. AUTHENTICATION BYPASS (CWE-287) [CRITICAL]
4. AUTHORIZATION FLAWS (CWE-285) [CRITICAL]
5. CSRF - Cross-Site Request Forgery (CWE-352) [HIGH]
6. INSECURE DESERIALIZATION (CWE-502) [CRITICAL]
7. PATH TRAVERSAL (CWE-22) [HIGH]
8. WEAK CRYPTOGRAPHY (CWE-327) [HIGH]
9. SENSITIVE DATA EXPOSURE (CWE-200) [HIGH]
10. INSECURE FILE UPLOAD (CWE-434) [CRITICAL]

RESPOND IN VALID JSON:
{{
    "agent": "KARAN",
    "vulnerabilities_found": 1,
    "severity": ["CRITICAL"],
    "details": [
        {{
            "file": "code.py",
            "line": 10,
            "issue": "Description",
            "severity": "CRITICAL",
            "exploit_example": "...",
            "fix_suggestion": "..."
        }}
    ]
}}
""")

        # =====================================================================
        # DEEPIKA PROMPTS
        # =====================================================================

        self.register_prompt("deepika", "performance_review", """
You are DEEPIKA, an adversarial performance issue detection agent.

YOUR ONLY GOAL: Find AS MANY performance bottlenecks as possible in this code.

REWARD SYSTEM:
- You get +2 points for CRITICAL issues
- You get +1 point for HIGH impact issues
- Think like a LOAD TESTER - how would this code fail at scale?

CODE TO ANALYZE:
```{file_type}
{code_content}
```

HUNT AGGRESSIVELY FOR:
1. ALGORITHMIC COMPLEXITY (Big-O) [CRITICAL]
2. N+1 QUERY PROBLEM [CRITICAL]
3. MEMORY LEAKS [CRITICAL]
4. BLOCKING OPERATIONS IN ASYNC [HIGH]
5. MISSING DATABASE INDEXES [HIGH]
6. NO STREAMING FOR LARGE DATA [HIGH]

RESPOND IN VALID JSON:
{{
    "agent": "DEEPIKA",
    "issues_found": 1,
    "severity": ["CRITICAL"],
    "details": [
        {{
            "file": "code.py",
            "line": 10,
            "issue": "Description",
            "severity": "CRITICAL",
            "current_performance": "...",
            "optimized_performance": "...",
            "fix_suggestion": "..."
        }}
    ]
}}
""")

        # =====================================================================
        # AARAV PROMPTS
        # =====================================================================

        self.register_prompt("aarav", "testing_strategy", """
You are Aarav, a senior QA engineer. Generate a browser testing strategy for this application:

URL: {url}
Test Scenarios: {tests}

Return ONLY valid JSON in this format:
{{
    "tests": [
        {{
            "name": "test_name",
            "description": "what it tests",
            "steps": ["step 1", "step 2"],
            "expected_result": "what should happen"
        }}
    ]
}}
""")

        # =====================================================================
        # PRANAV PROMPTS
        # =====================================================================

        self.register_prompt("pranav", "deployment_config", """
You are Pranav, a senior DevOps engineer. Generate deployment configuration files for this architecture:

{architecture}

Generate:
1. Dockerfile (multi-stage, optimized)
2. docker-compose.yml
3. .dockerignore
4. railway.json
5. vercel.json
6. DEPLOYMENT.md

Return JSON array:
[
    {{
        "file_path": "Dockerfile",
        "file_content": "...",
        "file_type": "docker",
        "purpose": "..."
    }}
]
""")

        # =====================================================================
        # DOCUMENT GENERATOR PROMPTS
        # =====================================================================

        self.register_prompt("document_generator", "sdd_generation", """
Generate a comprehensive Software Design Document for the project below.

PROJECT CONTEXT:
{project_context}

SECTIONS REQUIRED:
1. Project Overview
2. System Architecture
3. Database Schema
4. API Endpoints
5. Frontend Structure
6. Deployment Architecture
7. Security Measures
8. Testing Strategy

Respond with a professional, detailed markdown document.
""")

        # =====================================================================
        # NAVYA PROMPTS (restored)
        # =====================================================================
        
        self.register_prompt("navya", "code_review", """
You are NAVYA, an adversarial logic error detection agent.

YOUR ONLY GOAL: Find AS MANY logic errors as possible in this code.

CODE TO ANALYZE:
```{file_type}
{code_content}
```

HUNT AGGRESSIVELY FOR:
1. Division Operation issues
2. Null/None References
3. Array/List Access
4. Type Mismatches
5. Logic Inversions

RESPOND IN VALID JSON:
{
    "agent": "NAVYA",
    "bugs_found": 1,
    "severity": ["CRITICAL"],
    "details": [
        {
            "file": "main.py",
            "line": 45,
            "issue": "...",
            "severity": "CRITICAL",
            "fix_suggestion": "..."
        }
    ]
}
""")

        self.register_prompt("navya", "security_check", """
Perform a security audit on this code.

CODE:
{code_content}

CHECK FOR:
- SQL Injection
- XSS/CSRF
- Hardcoded secrets

OUTPUT JSON:
{
    "is_secure": true/false,
    "vulnerabilities": ["..."]
}
""")

        self.register_prompt("navya", "best_practices", """
Evaluate adherence to PEP8 (Python) or ESLint (JS/TS) standards.

CODE:
{code_content}

OUTPUT:
List of major deviations and improvement suggestions.
""")

        # =====================================================================
        # RIYA PROMPTS (MOBILE AGENT)
        # =====================================================================
        
        self.register_prompt("riya", "system_prompt", """
You are Riya, an expert mobile app developer with deep expertise in:
- Flutter (cross-platform: iOS, Android, Web)
- Progressive Web Apps (PWA)
- React Native
- Material Design 3 / iOS Human Interface Guidelines
- Mobile UX/UI patterns
- API integration
- Local storage (SQLite, SharedPreferences)
- State management (Riverpod, Bloc, Redux)
- Performance optimization for mobile
- App store best practices

You generate production-ready, well-structured mobile applications.
""")

        self.register_prompt("riya", "flutter_app", """
Generate a complete Flutter mobile application.

REQUIREMENTS:
{requirements}

DESIGN SYSTEM:
{design_system}

API BASE URL: {api_base_url}

Generate a Flutter project with this structure:

1. **pubspec.yaml**:
   - Flutter SDK
   - http package (API calls)
   - provider/riverpod (state management)
   - shared_preferences (local storage)
   - All necessary dependencies

2. **lib/main.dart**:
   - App entry point
   - MaterialApp setup
   - Theme configuration from design system
   - Navigation setup

3. **lib/screens/** (one file per screen):
   - Home screen
   - List views
   - Detail views
   - Form screens
   - All screens from requirements

4. **lib/services/api_service.dart**:
   - API client
   - HTTP methods (GET, POST, PUT, DELETE)
   - Error handling
   - Token management

5. **lib/models/** (data models):
   - Dart classes for API responses
   - JSON serialization
   - Validation

6. **lib/widgets/** (reusable components):
   - Custom buttons
   - Cards
   - Forms
   - Loading indicators

7. **lib/utils/**:
   - Constants
   - Helpers
   - Validators

Return as JSON:
{
  "pubspec.yaml": "...",
  "lib/main.dart": "...",
  "lib/screens/home_screen.dart": "...",
  "lib/services/api_service.dart": "...",
  "lib/models/user.dart": "...",
  ...
}

IMPORTANT:
- Use Material Design 3 widgets
- Implement proper error handling
- Add loading states
- Use design system colors
- Follow Flutter best practices
- Make code production-ready
""")

        self.register_prompt("riya", "web_app_for_pwa", """
Generate a React web application optimized for PWA conversion.

REQUIREMENTS:
{requirements}

DESIGN SYSTEM:
{design_system}

API BASE URL: {api_base_url}

Generate a React application with:

1. **package.json**: Dependencies (React, React Router, Axios)
2. **src/App.jsx**: Main app component with routing
3. **src/components/**: Reusable components
4. **src/pages/**: Page components
5. **src/services/api.js**: API integration
6. **src/styles/**: CSS modules
7. **public/index.html**: HTML template (PWA-ready)

Make it mobile-responsive and touch-friendly.

Return as JSON with file paths and contents.
""")

        self.register_prompt("riya", "react_native_app", """
Generate a React Native mobile application.

Similar structure to Flutter but using React Native components.

Include:
- React Navigation
- Axios for API
- AsyncStorage for local data
- React Native Paper or Native Base for UI

Return as JSON with file structure.
""")

# Global instance
prompt_engine = PromptEngine()
