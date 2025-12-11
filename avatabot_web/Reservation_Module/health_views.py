"""
Health check endpoint for Docker healthchecks and monitoring.
"""
from django.http import JsonResponse
from django.db import connection
from django.views.decorators.http import require_http_methods


@require_http_methods(["GET"])
def health_check(request):
    """
    Health check endpoint for Docker and monitoring.
    
    Returns:
        - 200 OK if database is accessible
        - 503 Service Unavailable if database is not accessible
    """
    try:
        # Test database connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        
        return JsonResponse({
            'status': 'healthy',
            'database': 'connected',
            'service': 'avatabot-backend'
        }, status=200)
    except Exception as e:
        return JsonResponse({
            'status': 'unhealthy',
            'database': 'disconnected',
            'error': str(e),
            'service': 'avatabot-backend'
        }, status=503)
