workers = 1  # Solo un proceso
threads = 1  # Solo un hilo
worker_class = 'sync'  # Elimina el uso de gthread para evitar hilos adicionales
worker_connections = 1  # Limita las conexiones
timeout = 900  # Mantén el tiempo de espera a 15 minutos
keepalive = 65
max_requests = 1  # Asegura que solo se maneje una solicitud a la vez sin rotaciones adicionales
max_requests_jitter = 0  # Desactiva el jitter
