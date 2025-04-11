docker-compose down -v
docker system prune -af --volumes
docker-compose up --build --remove-orphans -d