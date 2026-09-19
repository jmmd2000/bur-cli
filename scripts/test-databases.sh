#!/usr/bin/env bash
# Create, seed and tear down throwaway Postgres containers for testing bur.
set -euo pipefail

LABEL="bur.test=true"
PASSWORD="burtestpw"

usage() {
    cat <<'EOF'
Usage:
  test-databases.sh up [count] [versions...]   Create and seed containers (default: 1 16)
  test-databases.sh down                       Remove the containers this script created
  test-databases.sh status                     List the containers this script created

Versions cycle round-robin, so "up 4 15 16" makes two of each.
EOF
}

wait_for_ready() {
    local name=$1
    for ((attempt = 1; attempt <= 30; attempt++)); do
        if docker exec "$name" pg_isready -U "$name" -d "$name" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "timed out waiting for $name to accept connections" >&2
    return 1
}

seed() {
    local name=$1
    docker exec -i -e PGPASSWORD="$PASSWORD" "$name" \
        psql -v ON_ERROR_STOP=1 -U "$name" -d "$name" <<SQL >/dev/null
CREATE TABLE widgets (
    id serial PRIMARY KEY,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO widgets (name)
SELECT 'widget-' || generated FROM generate_series(1, 100) AS generated;

CREATE ROLE bur_readonly LOGIN PASSWORD '$PASSWORD';
GRANT SELECT ON ALL TABLES IN SCHEMA public TO bur_readonly;
SQL
}

up() {
    local count=${1:-1}
    shift || true
    local versions=("$@")
    if [[ ${#versions[@]} -eq 0 ]]; then
        versions=(16)
    fi
    if ! [[ $count =~ ^[0-9]+$ ]]; then
        echo "count must be a number" >&2
        exit 1
    fi

    for ((index = 0; index < count; index++)); do
        local version=${versions[index % ${#versions[@]}]}
        local name="bur_test_$((index + 1))_pg${version}"

        if docker ps -aq --filter "name=^${name}$" | grep -q .; then
            echo "$name already exists, skipping"
            continue
        fi

        echo "creating $name (postgres:$version)"
        docker run -d \
            --name "$name" \
            --label "$LABEL" \
            -e POSTGRES_PASSWORD="$PASSWORD" \
            -e POSTGRES_USER="$name" \
            -e POSTGRES_DB="$name" \
            "postgres:$version" >/dev/null

        wait_for_ready "$name"
        seed "$name"
    done

    echo
    status
}

down() {
    local names
    names=$(docker ps -aq --filter "label=$LABEL")
    if [[ -z "$names" ]]; then
        echo "nothing to remove"
        return 0
    fi
    echo "$names" | xargs docker rm -f >/dev/null
    echo "removed $(echo "$names" | wc -l) container(s)"
}

status() {
    docker ps -a --filter "label=$LABEL" \
        --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
}

case ${1:-} in
    up)
        shift
        up "$@"
        ;;
    down) down ;;
    status) status ;;
    *)
        usage
        exit 1
        ;;
esac