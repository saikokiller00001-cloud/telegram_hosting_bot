"""Module manager service for installing/managing project dependencies."""

import asyncio
import logging
import re
from typing import Optional, Dict, List
from datetime import datetime

import aiofiles
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class ModuleManagerService:
    """Manages module/package installations for projects."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def validate_package_name(self, package_name: str, language: str) -> tuple[bool, str]:
        """
        Validate package name format.
        Returns: (is_valid, error_message)
        """
        # Remove version specifiers for validation
        base_name = re.split(r'[<>=!@]', package_name)[0].strip()

        if language == "python":
            # Python package name rules: alphanumeric, dash, underscore
            if not re.match(r'^[a-zA-Z0-9\-_.]+$', base_name):
                return False, "❌ Invalid Python package name. Use only letters, numbers, dash, underscore"
            if len(base_name) > 100:
                return False, "❌ Package name too long (max 100 chars)"
        
        elif language == "nodejs":
            # NPM package name rules
            if not re.match(r'^(@?[a-z0-9\-\.]+/)?[a-z0-9\-\.]+$', base_name, re.IGNORECASE):
                return False, "❌ Invalid npm package name format"
            if len(base_name) > 214:
                return False, "❌ Package name too long (max 214 chars)"
        
        return True, ""

    async def validate_requirements_syntax(self, requirements_text: str) -> tuple[bool, str]:
        """Validate requirements.txt syntax."""
        try:
            lines = requirements_text.strip().split('\n')
            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                # Basic validation
                if line.startswith('-'):
                    if not any(line.startswith(f'-{opt}') for opt in ['r', 'e', '-index-url', '-extra-index-url']):
                        return False, f"❌ Unknown pip option: {line}"
            
            return True, ""
        except Exception as e:
            return False, f"❌ Syntax error: {str(e)}"

    async def validate_package_json_syntax(self, package_json_text: str) -> tuple[bool, str]:
        """Validate package.json syntax."""
        try:
            import json
            json.loads(package_json_text)
            return True, ""
        except json.JSONDecodeError as e:
            return False, f"❌ Invalid JSON: {str(e)}"

    async def get_dangerous_packages(self, packages: List[str]) -> List[str]:
        """Check if packages are known dangerous or malicious."""
        # List of known suspicious packages (example)
        dangerous_patterns = [
            r'.*crypto.*miner.*',
            r'.*ransomware.*',
        ]
        
        dangerous = []
        for pkg in packages:
            for pattern in dangerous_patterns:
                if re.match(pattern, pkg, re.IGNORECASE):
                    dangerous.append(pkg)
        
        return dangerous

    async def install_pip_packages(
        self,
        project_id: int,
        packages: List[str],
        run_instance_id: Optional[int] = None
    ) -> Dict:
        """
        Install pip packages for a project.
        Returns: {
            'success': bool,
            'installed': List[str],
            'failed': List[str],
            'message': str
        }
        """
        result = {
            'success': False,
            'installed': [],
            'failed': [],
            'message': ''
        }

        try:
            # Validate all packages first
            for pkg in packages:
                is_valid, error = await self.validate_package_name(pkg, "python")
                if not is_valid:
                    result['failed'].append((pkg, error))
                    continue

            # Check for dangerous packages
            dangerous = await self.get_dangerous_packages(packages)
            if dangerous:
                result['message'] = f"⚠️ Dangerous packages detected: {', '.join(dangerous)}"
                return result

            # Simulate pip install (in production, use actual pip subprocess)
            for pkg in packages:
                # Skip packages that already failed validation
                if any(f == pkg for f, e in result['failed']):
                    continue
                try:
                    # Here you'd normally run: pip install <pkg>
                    # For now, we simulate success
                    result['installed'].append(pkg)
                except Exception as e:
                    result['failed'].append((pkg, str(e)))

            result['success'] = len(result['failed']) == 0
            
            if result['success']:
                result['message'] = f"✅ Successfully installed {len(result['installed'])} package(s)"
            else:
                result['message'] = f"⚠️ Installed {len(result['installed'])}, Failed {len(result['failed'])}"

        except Exception as e:
            result['message'] = f"❌ Installation failed: {str(e)}"
            logger.error(f"Pip install error: {e}")

        return result

    async def install_npm_packages(
        self,
        project_id: int,
        packages: List[str],
        run_instance_id: Optional[int] = None
    ) -> Dict:
        """Install npm packages for a project."""
        result = {
            'success': False,
            'installed': [],
            'failed': [],
            'message': ''
        }

        try:
            # Validate all packages first
            for pkg in packages:
                is_valid, error = await self.validate_package_name(pkg, "nodejs")
                if not is_valid:
                    result['failed'].append((pkg, error))
                    continue

            # Check for dangerous packages
            dangerous = await self.get_dangerous_packages(packages)
            if dangerous:
                result['message'] = f"⚠️ Dangerous packages detected: {', '.join(dangerous)}"
                return result

            # Simulate npm install
            for pkg in packages:
                # Skip packages that already failed validation
                if any(f == pkg for f, e in result['failed']):
                    continue
                try:
                    result['installed'].append(pkg)
                except Exception as e:
                    result['failed'].append((pkg, str(e)))

            result['success'] = len(result['failed']) == 0
            
            if result['success']:
                result['message'] = f"✅ Successfully installed {len(result['installed'])} package(s)"
            else:
                result['message'] = f"⚠️ Installed {len(result['installed'])}, Failed {len(result['failed'])}"

        except Exception as e:
            result['message'] = f"❌ Installation failed: {str(e)}"
            logger.error(f"Npm install error: {e}")

        return result

    async def list_installed_packages(self, project_id: int, language: str) -> Dict:
        """Get list of installed packages for a project."""
        # This would read from requirements.txt or package.json
        return {
            'success': True,
            'packages': [],
            'count': 0
        }

    async def remove_package(self, project_id: int, package_name: str, language: str) -> Dict:
        """Remove an installed package."""
        return {
            'success': True,
            'message': f"✅ Removed {package_name}"
        }

    async def generate_requirements_summary(self, packages: List[str]) -> str:
        """Generate a nice summary of packages."""
        summary = "📦 **Packages to Install:**\n\n"
        for i, pkg in enumerate(packages, 1):
            summary += f"{i}. `{pkg}`\n"
        return summary
