import os
import uuid
import json
import requests

from flask import Flask, request, jsonify, send_file
from google.cloud import datastore, storage
from google.cloud.datastore.query import PropertyFilter
from jose import jwt
from jose.exceptions import JWTError, ExpiredSignatureError
import io

app = Flask(__name__)

# ── Configuration ────────────────────────────────────────────────────────────
DATASTORE_CLIENT = datastore.Client()
STORAGE_CLIENT   = storage.Client()

AUTH0_DOMAIN        = os.environ.get("AUTH0_DOMAIN",        "YOUR_AUTH0_DOMAIN")
AUTH0_CLIENT_ID     = os.environ.get("AUTH0_CLIENT_ID",     "YOUR_CLIENT_ID")
AUTH0_CLIENT_SECRET = os.environ.get("AUTH0_CLIENT_SECRET", "YOUR_CLIENT_SECRET")
AUTH0_AUDIENCE      = os.environ.get("AUTH0_AUDIENCE",      "YOUR_AUDIENCE")
GCS_BUCKET          = os.environ.get("GCS_BUCKET",          "YOUR_BUCKET_NAME")

ALGORITHMS = ["RS256"]

# ── Error messages ────────────────────────────────────────────────────────────
ERR_400 = {"Error": "The request body is invalid"}
ERR_401 = {"Error": "Unauthorized"}
ERR_403 = {"Error": "You don't have permission on this resource"}
ERR_404 = {"Error": "Not found"}
ERR_409_INSTRUCTOR = {"Error": "The value of instructor_id is invalid"}
ERR_409_ENROLLMENT = {"Error": "Enrollment data is invalid"}

# ── JWT helpers ───────────────────────────────────────────────────────────────

def get_jwks():
    url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
    resp = requests.get(url)
    return resp.json()

def verify_jwt(token):
    jwks = get_jwks()
    unverified_header = jwt.get_unverified_header(token)
    rsa_key = {}
    for key in jwks["keys"]:
        if key["kid"] == unverified_header["kid"]:
            rsa_key = {
                "kty": key["kty"],
                "kid": key["kid"],
                "use": key["use"],
                "n":   key["n"],
                "e":   key["e"],
            }
    if not rsa_key:
        raise JWTError("No matching key found")
    payload = jwt.decode(
        token,
        rsa_key,
        algorithms=ALGORITHMS,
        audience=AUTH0_AUDIENCE,
        issuer=f"https://{AUTH0_DOMAIN}/"
    )
    return payload

def get_token_from_header():
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None

def decode_token_or_401():
    token = get_token_from_header()
    if not token:
        return None, (jsonify(ERR_401), 401)
    try:
        payload = verify_jwt(token)
        return payload, None
    except Exception:
        return None, (jsonify(ERR_401), 401)

# ── Datastore helpers ─────────────────────────────────────────────────────────

def get_user_by_sub(sub):
    q = DATASTORE_CLIENT.query(kind="users")
    q.add_filter(filter=PropertyFilter("sub", "=", sub))
    results = list(q.fetch(limit=1))
    return results[0] if results else None

def get_user_by_id(user_id):
    key = DATASTORE_CLIENT.key("users", int(user_id))
    return DATASTORE_CLIENT.get(key)

def get_course_by_id(course_id):
    key = DATASTORE_CLIENT.key("courses", int(course_id))
    return DATASTORE_CLIENT.get(key)

def base_url():
    return request.host_url.rstrip("/")

def course_self_url(course_id):
    return f"{base_url()}/courses/{course_id}"

def avatar_url(user_id):
    return f"{base_url()}/users/{user_id}/avatar"

# ── GCS helpers ───────────────────────────────────────────────────────────────

def upload_avatar(user_id, file_bytes, content_type="image/png"):
    bucket = STORAGE_CLIENT.bucket(GCS_BUCKET)
    blob_name = f"avatars/{uuid.uuid4().hex}.png"
    blob = bucket.blob(blob_name)
    blob.upload_from_string(file_bytes, content_type=content_type)
    return blob_name

def delete_avatar_blob(blob_name):
    bucket = STORAGE_CLIENT.bucket(GCS_BUCKET)
    blob = bucket.blob(blob_name)
    blob.delete()

def download_avatar_blob(blob_name):
    bucket = STORAGE_CLIENT.bucket(GCS_BUCKET)
    blob = bucket.blob(blob_name)
    return blob.download_as_bytes()

# ── Enrollment helpers ────────────────────────────────────────────────────────

def fetch_enrollment(course_id):
    q = DATASTORE_CLIENT.query(kind="enrollment")
    q.add_filter(filter=PropertyFilter("course_id", "=", int(course_id)))
    results = list(q.fetch())
    if results:
        return set(results[0].get("student_ids", []))
    return set()

def save_enrollment(course_id, student_ids_set):
    q = DATASTORE_CLIENT.query(kind="enrollment")
    q.add_filter(filter=PropertyFilter("course_id", "=", int(course_id)))
    results = list(q.fetch(limit=1))
    if results:
        entity = results[0]
    else:
        key = DATASTORE_CLIENT.key("enrollment")
        entity = datastore.Entity(key=key)
        entity["course_id"] = int(course_id)
    entity["student_ids"] = list(student_ids_set)
    DATASTORE_CLIENT.put(entity)

def delete_enrollment(course_id):
    q = DATASTORE_CLIENT.query(kind="enrollment")
    q.add_filter(filter=PropertyFilter("course_id", "=", int(course_id)))
    results = list(q.fetch())
    for entity in results:
        DATASTORE_CLIENT.delete(entity.key)

# ── Format helpers ────────────────────────────────────────────────────────────

def format_user_summary(entity):
    return {
        "id":   entity.key.id,
        "role": entity["role"],
        "sub":  entity["sub"],
    }

def format_course(entity):
    cid = entity.key.id
    return {
        "id":            cid,
        "subject":       entity["subject"],
        "number":        entity["number"],
        "title":         entity["title"],
        "term":          entity["term"],
        "instructor_id": entity["instructor_id"],
        "self":          course_self_url(cid),
    }

def get_courses_for_instructor(instructor_id):
    q = DATASTORE_CLIENT.query(kind="courses")
    q.add_filter(filter=PropertyFilter("instructor_id", "=", int(instructor_id)))
    results = list(q.fetch())
    return [course_self_url(e.key.id) for e in results]

def get_courses_for_student(student_id):
    q = DATASTORE_CLIENT.query(kind="enrollment")
    results = list(q.fetch())
    course_urls = []
    for e in results:
        if int(student_id) in e.get("student_ids", []):
            course_urls.append(course_self_url(e["course_id"]))
    return course_urls

# ═══════════════════════════════════════════════════════════════════════════════
# 1. POST /users/login
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/users/login", methods=["POST"])
def user_login():
    body = request.get_json(silent=True)
    if not body or "username" not in body or "password" not in body:
        return jsonify(ERR_400), 400

    username = body["username"]
    password = body["password"]

    url = f"https://{AUTH0_DOMAIN}/oauth/token"
    payload = {
        "grant_type":    "password",
        "username":      username,
        "password":      password,
        "client_id":     AUTH0_CLIENT_ID,
        "client_secret": AUTH0_CLIENT_SECRET,
        "scope":         "openid",
    }
    resp = requests.post(url, json=payload)
    data = resp.json()

    if resp.status_code != 200 or "id_token" not in data:
        return jsonify(ERR_401), 401

    return jsonify({"token": data["id_token"]}), 200

# ═══════════════════════════════════════════════════════════════════════════════
# 2. GET /users  (admin only)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/users", methods=["GET"])
def get_all_users():
    payload, err = decode_token_or_401()
    if err:
        return err

    user = get_user_by_sub(payload["sub"])
    if not user or user["role"] != "admin":
        return jsonify(ERR_403), 403

    q = DATASTORE_CLIENT.query(kind="users")
    results = list(q.fetch())
    return jsonify([format_user_summary(u) for u in results]), 200

# ═══════════════════════════════════════════════════════════════════════════════
# 3. GET /users/:user_id  (admin or self)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/users/<int:user_id>", methods=["GET"])
def get_user(user_id):
    payload, err = decode_token_or_401()
    if err:
        return err

    requester = get_user_by_sub(payload["sub"])
    target    = get_user_by_id(user_id)

    if not target:
        return jsonify(ERR_403), 403
    if not requester:
        return jsonify(ERR_403), 403
    is_admin = (requester["role"] == "admin")
    is_self  = (requester.key.id == user_id)
    if not is_admin and not is_self:
        return jsonify(ERR_403), 403

    resp = {
        "id":   target.key.id,
        "role": target["role"],
        "sub":  target["sub"],
    }

    if target.get("avatar_blob"):
        resp["avatar_url"] = avatar_url(user_id)

    role = target["role"]
    if role == "instructor":
        resp["courses"] = get_courses_for_instructor(user_id)
    elif role == "student":
        resp["courses"] = get_courses_for_student(user_id)

    return jsonify(resp), 200

# ═══════════════════════════════════════════════════════════════════════════════
# 4. POST /users/:user_id/avatar  (self only)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/users/<int:user_id>/avatar", methods=["POST"])
def create_update_avatar(user_id):
    if "file" not in request.files:
        return jsonify(ERR_400), 400

    payload, err = decode_token_or_401()
    if err:
        return err

    requester = get_user_by_sub(payload["sub"])
    if not requester or requester.key.id != user_id:
        return jsonify(ERR_403), 403

    file = request.files["file"]
    file_bytes = file.read()

    target = get_user_by_id(user_id)
    if target and target.get("avatar_blob"):
        try:
            delete_avatar_blob(target["avatar_blob"])
        except Exception:
            pass

    blob_name = upload_avatar(user_id, file_bytes)

    key    = DATASTORE_CLIENT.key("users", user_id)
    entity = DATASTORE_CLIENT.get(key)
    entity["avatar_blob"] = blob_name
    DATASTORE_CLIENT.put(entity)

    return jsonify({"avatar_url": avatar_url(user_id)}), 200

# ═══════════════════════════════════════════════════════════════════════════════
# 5. GET /users/:user_id/avatar  (self only)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/users/<int:user_id>/avatar", methods=["GET"])
def get_avatar(user_id):
    payload, err = decode_token_or_401()
    if err:
        return err

    requester = get_user_by_sub(payload["sub"])
    if not requester or requester.key.id != user_id:
        return jsonify(ERR_403), 403

    target = get_user_by_id(user_id)
    if not target or not target.get("avatar_blob"):
        return jsonify(ERR_404), 404

    file_bytes = download_avatar_blob(target["avatar_blob"])
    return send_file(
        io.BytesIO(file_bytes),
        mimetype="image/png",
        as_attachment=False,
        download_name="avatar.png"
    )

# ═══════════════════════════════════════════════════════════════════════════════
# 6. DELETE /users/:user_id/avatar  (self only)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/users/<int:user_id>/avatar", methods=["DELETE"])
def delete_avatar(user_id):
    payload, err = decode_token_or_401()
    if err:
        return err

    requester = get_user_by_sub(payload["sub"])
    if not requester or requester.key.id != user_id:
        return jsonify(ERR_403), 403

    target = get_user_by_id(user_id)
    if not target or not target.get("avatar_blob"):
        return jsonify(ERR_404), 404

    delete_avatar_blob(target["avatar_blob"])
    del target["avatar_blob"]
    DATASTORE_CLIENT.put(target)

    return "", 204

# ═══════════════════════════════════════════════════════════════════════════════
# 7. POST /courses  (admin only)
# ═══════════════════════════════════════════════════════════════════════════════

REQUIRED_COURSE_FIELDS = {"subject", "number", "title", "term", "instructor_id"}

@app.route("/courses", methods=["POST"])
def create_course():
    body = request.get_json(silent=True)
    if not body or not REQUIRED_COURSE_FIELDS.issubset(body.keys()):
        return jsonify(ERR_400), 400

    payload, err = decode_token_or_401()
    if err:
        return err

    requester = get_user_by_sub(payload["sub"])
    if not requester or requester["role"] != "admin":
        return jsonify(ERR_403), 403

    instructor = get_user_by_id(body["instructor_id"])
    if not instructor or instructor["role"] != "instructor":
        return jsonify(ERR_409_INSTRUCTOR), 409

    key    = DATASTORE_CLIENT.key("courses")
    entity = datastore.Entity(key=key)
    entity.update({
        "subject":       body["subject"],
        "number":        body["number"],
        "title":         body["title"],
        "term":          body["term"],
        "instructor_id": int(body["instructor_id"]),
    })
    DATASTORE_CLIENT.put(entity)

    return jsonify(format_course(entity)), 201

# ═══════════════════════════════════════════════════════════════════════════════
# 8. GET /courses  (unprotected, paginated, sorted by subject)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/courses", methods=["GET"])
def get_all_courses():
    try:
        offset = int(request.args.get("offset", 0))
        limit  = int(request.args.get("limit",  3))
    except ValueError:
        offset, limit = 0, 3

    q = DATASTORE_CLIENT.query(kind="courses")
    q.order = ["subject"]

    iterator = q.fetch(limit=limit, offset=offset)
    pages    = iterator.pages
    results  = list(next(pages))

    courses = [format_course(e) for e in results]

    resp = {"courses": courses}
    if len(courses) == limit:
        resp["next"] = f"{base_url()}/courses?limit={limit}&offset={offset + limit}"

    return jsonify(resp), 200

# ═══════════════════════════════════════════════════════════════════════════════
# 9. GET /courses/:course_id  (unprotected)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/courses/<int:course_id>", methods=["GET"])
def get_course(course_id):
    entity = get_course_by_id(course_id)
    if not entity:
        return jsonify(ERR_404), 404
    return jsonify(format_course(entity)), 200

# ═══════════════════════════════════════════════════════════════════════════════
# 10. PATCH /courses/:course_id  (admin only)
# ═══════════════════════════════════════════════════════════════════════════════

ALLOWED_COURSE_UPDATE = {"subject", "number", "title", "term", "instructor_id"}

@app.route("/courses/<int:course_id>", methods=["PATCH"])
def update_course(course_id):
    payload, err = decode_token_or_401()
    if err:
        return err

    requester = get_user_by_sub(payload["sub"])
    entity    = get_course_by_id(course_id)

    if not entity:
        return jsonify(ERR_403), 403
    if not requester or requester["role"] != "admin":
        return jsonify(ERR_403), 403

    body = request.get_json(silent=True)
    if body is None:
        body = {}

    if "instructor_id" in body:
        instructor = get_user_by_id(body["instructor_id"])
        if not instructor or instructor["role"] != "instructor":
            return jsonify(ERR_409_INSTRUCTOR), 409

    for field in ALLOWED_COURSE_UPDATE:
        if field in body:
            val = body[field]
            if field == "instructor_id":
                val = int(val)
            entity[field] = val

    DATASTORE_CLIENT.put(entity)
    return jsonify(format_course(entity)), 200

# ═══════════════════════════════════════════════════════════════════════════════
# 11. DELETE /courses/:course_id  (admin only)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/courses/<int:course_id>", methods=["DELETE"])
def delete_course(course_id):
    payload, err = decode_token_or_401()
    if err:
        return err

    requester = get_user_by_sub(payload["sub"])
    entity    = get_course_by_id(course_id)

    if not entity:
        return jsonify(ERR_403), 403
    if not requester or requester["role"] != "admin":
        return jsonify(ERR_403), 403

    delete_enrollment(course_id)
    DATASTORE_CLIENT.delete(entity.key)
    return "", 204

# ═══════════════════════════════════════════════════════════════════════════════
# 12. PATCH /courses/:course_id/students  (admin or instructor of course)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/courses/<int:course_id>/students", methods=["PATCH"])
def update_enrollment(course_id):
    payload, err = decode_token_or_401()
    if err:
        return err

    requester = get_user_by_sub(payload["sub"])
    entity    = get_course_by_id(course_id)

    if not entity:
        return jsonify(ERR_403), 403

    is_admin      = requester and requester["role"] == "admin"
    is_instructor = requester and requester.key.id == entity["instructor_id"]

    if not is_admin and not is_instructor:
        return jsonify(ERR_403), 403

    body       = request.get_json(silent=True)
    add_ids    = body.get("add", [])
    remove_ids = body.get("remove", [])

    if set(add_ids) & set(remove_ids):
        return jsonify(ERR_409_ENROLLMENT), 409

    all_ids = set(add_ids) | set(remove_ids)
    for sid in all_ids:
        user = get_user_by_id(sid)
        if not user or user["role"] != "student":
            return jsonify(ERR_409_ENROLLMENT), 409

    enrolled = fetch_enrollment(course_id)
    enrolled |= set(int(i) for i in add_ids)
    enrolled -= set(int(i) for i in remove_ids)
    save_enrollment(course_id, enrolled)

    return "", 200

# ═══════════════════════════════════════════════════════════════════════════════
# 13. GET /courses/:course_id/students  (admin or instructor of course)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/courses/<int:course_id>/students", methods=["GET"])
def get_enrollment(course_id):
    payload, err = decode_token_or_401()
    if err:
        return err

    requester = get_user_by_sub(payload["sub"])
    entity    = get_course_by_id(course_id)

    if not entity:
        return jsonify(ERR_403), 403

    is_admin      = requester and requester["role"] == "admin"
    is_instructor = requester and requester.key.id == entity["instructor_id"]

    if not is_admin and not is_instructor:
        return jsonify(ERR_403), 403

    enrolled = fetch_enrollment(course_id)
    return jsonify(list(enrolled)), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)