from flask import Flask, request, jsonify, redirect, send_file
from google.cloud import datastore
from google.cloud import storage
import requests
import json
from six.moves.urllib.request import urlopen
from jose import jwt
from os import environ as env
from dotenv import find_dotenv, load_dotenv
from datetime import datetime, timezone
from io import BytesIO
import os

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False
app.secret_key = env.get("APP_SECRET_KEY")

ENV_FILE = find_dotenv()
if ENV_FILE:
  load_dotenv(ENV_FILE)

# Auth0 Identifiers
CLIENT_ID = env.get("AUTH0_CLIENT_ID")
CLIENT_SECRET = env.get("AUTH0_CLIENT_SECRET")
DOMAIN = env.get("AUTH0_DOMAIN")

if not (CLIENT_ID and CLIENT_SECRET and DOMAIN):
  raise SystemExit("Missing Client_ID / Client_secret / Domain in .env")

# Datastore client 
PROJECT_ID = "assignment6-pettibor" 
client = datastore.Client(project=PROJECT_ID)

# Google Cloud Storage client for avatars
storage_client = storage.Client(project=PROJECT_ID)
BUCKET_NAME = f"assignment6-pettibor-avatars"

# Datastore kinds
USERS = "users"
COURSES = "courses"
ASSIGNMENTS = "assignments"

# Error Messages
ERROR_404 = {"Error": "Not found"}
ERROR_403 = {"Error": "You don't have permission on this resource"}
ERROR_401 = {"Error": "Unauthorized"}
ERROR_400 = {"Error": "The request body is invalid"}


ALGORITHMS = ["RS256"]

# ============================================================================
# AUTH0 MANAGEMENT API & USER SEEDING
# ============================================================================
# Request a Management API token
# Doc: https://auth0.com/docs/secure/tokens/access-tokens/management-api-access-tokens/get-management-api-access-tokens-for-production
# Returns: A signed JWT with expiration, scope, and token type.
def get_management_token():
  """Get Auth0 Management API token"""
  token_url = f"https://{DOMAIN}/oauth/token"
  token_payload = {
      "grant_type": "client_credentials",
      "client_id": CLIENT_ID,
      "client_secret": CLIENT_SECRET,
      "audience": f"https://{DOMAIN}/api/v2/"
  }
  r = requests.post(token_url, json=token_payload, headers={"content-type": "application/json"})
  r.raise_for_status()
  return r.json().get("access_token")

def seed_users_once():
    """
    Seed Auth0 and Datastore with 9 users (1 admin, 2 instructors, 6 students).
    Only runs once - uses a Datastore marker to track completion.
    """
    marker_key = client.key("metadata", "users_seeded")
    
    # Load user data and check expected count
    try:
        with open("users.json", "r", encoding="utf-8") as f:
            users_data = json.load(f)
        expected_user_count = len(users_data)
    except Exception as e:
        print(f"Failed to load users.json: {e}")
        return
    
    # Check if seeding is needed
    try:
        # Check Datastore marker
        marker = client.get(marker_key)
        
        # Always verify actual user count in Datastore
        count_query = client.query(kind=USERS)
        existing_users = list(count_query.fetch())
        
        if len(existing_users) >= expected_user_count:
            print(f"Users already seeded ({len(existing_users)} users found). Skipping.")
            return
        elif marker:
            # Marker exists but users are missing - delete marker and re-seed
            print(f"Marker exists but only {len(existing_users)}/{expected_user_count} users found. Re-seeding...")
            client.delete(marker_key)
    except Exception as e:
        print(f"Error checking seeding status: {e}")
        # Continue with seeding if check fails
        
    # Get management token
    try:
        mgmt_token = get_management_token()
        headers = {"Authorization": f"Bearer {mgmt_token}", "Content-Type": "application/json"}
        mgmt_users_url = f"https://{DOMAIN}/api/v2/users"
    except Exception as e:
        print(f"Failed to get management token: {e}")
        return
    
    # Define roles based on email
    def get_role(email):
        if "admin" in email:
            return "admin"
        elif "instructor" in email:
            return "instructor"
        else:
            return "student"
    
    created_count = 0
    
    for user_def in users_data:
        email = user_def.get("email")
        if not email:
            print("Skipping user with no email")
            continue
        
        role = get_role(email)
        
        # 1. Check if user exists in Auth0
        auth0_user = None
        try:
            resp = requests.get(
                f"https://{DOMAIN}/api/v2/users-by-email",
                headers=headers,
                params={"email": email},
                timeout=10
            )
            if resp.status_code == 200 and resp.json():
                auth0_user = resp.json()[0]
                print(f"User exists in Auth0: {email}")
        except Exception as e:
            print(f"Warning: couldn't check Auth0 for {email}: {e}")
        
        # 2. Create in Auth0 if doesn't exist
        if not auth0_user:
            payload = {
                "email": email,
                "connection": user_def.get("connection", "Username-Password-Authentication"),
                "password": user_def.get("password"),
                "email_verified": False,
                "verify_email": False
            }
            try:
                cresp = requests.post(mgmt_users_url, headers=headers, json=payload, timeout=10)
                if cresp.status_code in (200, 201):
                    auth0_user = cresp.json()
                    print(f"Created in Auth0: {email}")
                    created_count += 1
                else:
                    print(f"Failed to create in Auth0 {email}: {cresp.status_code} {cresp.text}")
                    continue
            except Exception as e:
                print(f"Error creating {email} in Auth0: {e}")
                continue
        
        # 3. Store user in Datastore with Auth0 sub
        auth0_user_id = auth0_user.get("user_id")
        if not auth0_user_id:
            print(f"Warning: no user_id from Auth0 for {email}, skipping Datastore write")
            continue      
        
        # Create new user entity
        user_entity = datastore.Entity(key=client.key(USERS))
        user_entity.update({
            "sub": auth0_user_id,
            "role": role
        })
        
        try:
            client.put(user_entity)
            print(f"Stored in Datastore: {email} (role: {role}, id: {user_entity.key.id})")
        except Exception as e:
            print(f"Failed to store {email} in Datastore: {e}")
    
    # 4. Write completion marker
    try:
        marker_entity = datastore.Entity(key=marker_key)
        marker_entity.update({
            "seeded": True,
            "created_count": created_count,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        client.put(marker_entity)
        print(f"Seeding complete. Marker written to Datastore. Created {created_count} new users.")
    except Exception as e:
        print(f"Failed to write completion marker: {e}")

# Seed users on startup
seed_users_once()

# ============================================================================
# AUTH ERROR HANDLING
# ============================================================================

class AuthError(Exception):
  def __init__(self, error, status_code):
      super().__init__()
      self.error = error
      self.status_code = status_code

@app.errorhandler(AuthError)
def handle_auth_error(ex):
  response = jsonify(ex.error)
  response.status_code = ex.status_code
  return response

# ============================================================================
# JWT VERIFICATION
# ============================================================================

def verify_jwt(req):
  """Verify JWT and return payload"""
  auth_header = req.headers.get("Authorization", None)
  if not auth_header:
      raise AuthError({"Error": "Unauthorized"}, 401)

  parts = auth_header.split()
  if len(parts) != 2 or parts[0].lower() != "bearer":
      raise AuthError({"Error": "Unauthorized"}, 401)

  token = parts[1]

  jsonurl = urlopen("https://" + DOMAIN + "/.well-known/jwks.json")
  jwks = json.loads(jsonurl.read())

  try:
      unverified_header = jwt.get_unverified_header(token)
  except Exception:
      raise AuthError({"Error": "Unauthorized"}, 401)

  if unverified_header.get("alg") == "HS256":
      raise AuthError({"Error": "Unauthorized"}, 401)

  rsa_key = {}
  for key in jwks.get("keys", []):
      if key.get("kid") == unverified_header.get("kid"):
          rsa_key = {
              "kty": key.get("kty"),
              "kid": key.get("kid"),
              "use": key.get("use"),
              "n": key.get("n"),
              "e": key.get("e"),
          }
          break

  if not rsa_key:
      raise AuthError({"Error": "Unauthorized"}, 401)

  try:
      payload = jwt.decode(
          token,
          rsa_key,
          algorithms=ALGORITHMS,
          audience=CLIENT_ID,
          issuer="https://" + DOMAIN + "/",
      )
      return payload
  except jwt.ExpiredSignatureError:
      raise AuthError({"Error": "Unauthorized"}, 401)
  except jwt.JWTClaimsError:
      raise AuthError({"Error": "Unauthorized"}, 401)
  except Exception:
      raise AuthError({"Error": "Unauthorized"}, 401)

def get_user_from_jwt(req):
  """Get user entity from Datastore based on JWT sub"""
  payload = verify_jwt(req)
  sub = payload.get("sub")

  query = client.query(kind=USERS)
  query.add_filter("sub", "=", sub)
  users = list(query.fetch())

  if not users:
      raise AuthError({"Error": "User not found"}, 403)
  
  return users[0]

# ============================================================================
# ENDPOINT 1: POST /users/login
# ============================================================================

@app.route('/users/login', methods=['POST'])
def login_user():
  """Generate a JWT from Auth0"""
  content = request.get_json(silent=True)
  if not content:
      return jsonify({"Error": "The request body is invalid"}), 400

  username = content.get("username")
  password = content.get("password")
  
  if not username or not password:
      return jsonify({"Error": "The request body is invalid"}), 400

  body = {
      "grant_type": "password",
      "username": username,
      "password": password,
      "client_id": CLIENT_ID,
      "client_secret": CLIENT_SECRET
  }
  headers = {"content-type": "application/json"}
  url = "https://" + DOMAIN + "/oauth/token"
  
  r = requests.post(url, json=body, headers=headers)
  if r.status_code == 200:
      auth0_response = r.json()
      return jsonify({"token": auth0_response["id_token"]}), 200
  else:   
      return jsonify({"Error": "Unauthorized"}), 401


# ============================================================================
# ENDPOINT 2: GET /users/:user_id
# ============================================================================

@app.route('/users/<int:user_id>', methods=['GET'])
def get_user(user_id):
  """Get a user by ID - requires admin or self"""
  try:
      current_user = get_user_from_jwt(request)
  except AuthError as e:
      return jsonify(e.error), e.status_code
  
  # Check if admin or requesting own data
  if current_user["role"] != "admin" and current_user.key.id != user_id:
      return jsonify({"Error": "You don't have permission on this resource"}), 403
  
  # Get the requested user
  user_key = client.key(USERS, user_id)
  user = client.get(key=user_key)
  
  if not user:
      return jsonify({"Error": "No user with this user_id exists"}), 404
  
  # Build response
  user_data = {
      "id": user.key.id,
      "role": user["role"],
      "sub": user["sub"]
  }
  
  # Add avatar if exists.
  if "avatar_url" in user:
      user_data["avatar_url"] = user["avatar_url"]
  
  # If student, add courses.
  if user["role"] == "student":
      query = client.query(kind=COURSES)
      query.add_filter("students", "=", user.key.id)
      courses = list(query.fetch())
      user_data["courses"] = [c.key.id for c in courses]

  # If instructor, add courses they teach
  if user["role"] == "instructor":
      query = client.query(kind=COURSES)
      query.add_filter("instructor_id", "=", user.key.id)
      courses = list(query.fetch())
      user_data["courses"] = [c.key.id for c in courses]

  
  return jsonify(user_data), 200

# ============================================================================
# ENDPOINT 3: GET /users
# ============================================================================

@app.route('/users', methods=['GET'])
def get_all_users():
  """Get all users - admin only"""
  try:
      current_user = get_user_from_jwt(request)
  except AuthError as e:
      return jsonify(e.error), e.status_code
  
  if current_user["role"] != "admin":
      return jsonify({"Error": "You don't have permission on this resource"}), 403
  
  query = client.query(kind=USERS)
  all_users = list(query.fetch())
  
  users_list = []
  for user in all_users:
      user_data = {
          "id": user.key.id,
          "role": user["role"],
          "sub": user["sub"]
      }
      if "avatar_url" in user:
          user_data["avatar_url"] = user["avatar_url"]

      users_list.append(user_data)
  
  return jsonify(users_list), 200

# ============================================================================
# ENDPOINT 4: POST /users/:user_id/avatar
# ============================================================================

@app.route('/users/<int:user_id>/avatar', methods=['POST'])
def upload_avatar(user_id):
  """Upload avatar image for a user - self only"""

  # Check for file first - 400 takes precedence
  if 'file' not in request.files:
      return jsonify(ERROR_400), 400

  file = request.files['file']
  if not file or file.filename == '':
      return jsonify(ERROR_400), 400

  # Then check JWT (401)
  try:
      current_user = get_user_from_jwt(request)
  except AuthError as e:
      return jsonify(e.error), e.status_code

  # Check permission
  if current_user.key.id != user_id:
      return jsonify(ERROR_403), 403

  # Ensure target user exists (404)
  user_key = client.key(USERS, user_id)
  user = client.get(key=user_key)
  if not user:
      return jsonify(ERROR_404), 404

  # Upload to Cloud Storage
  try:
      bucket = storage_client.get_bucket(BUCKET_NAME)
      blob_name = f"user_{user_id}_avatar.png"
      blob = bucket.blob(blob_name)

      file.seek(0)
      blob.upload_from_file(file, content_type='image/png')

      # Save canonical blob name + Flask-served avatar URL in Datastore
      user["avatar_blob"] = blob_name
      user["avatar_url"] = f"{request.url_root}users/{user_id}/avatar"
      client.put(user)

      return jsonify({"avatar_url": user["avatar_url"]}), 200

  except Exception:
      return jsonify({"Error": "Failed to upload avatar"}), 500

# ============================================================================
# ENDPOINT 5: GET /users/:user_id/avatar
# ============================================================================

@app.route('/users/<int:user_id>/avatar', methods=['GET'])
def get_avatar(user_id):
  """Get avatar for a user - returns the actual file from GCS"""
  
  # Check JWT
  try:
      current_user = get_user_from_jwt(request)
  except AuthError as e:
      return jsonify(e.error), e.status_code
  
  # Check permission 
  if current_user.key.id != user_id:
      return jsonify(ERROR_403), 403
  
  # Get user
  user_key = client.key(USERS, user_id)
  user = client.get(key=user_key)
  
  if not user:
      return jsonify(ERROR_404), 404
  
  # Check if user has an avatar
  if "avatar_url" not in user:
      return jsonify(ERROR_404), 404
  
  # Download file from GCS and return it
  try:
      bucket = storage_client.get_bucket(BUCKET_NAME)
      blob_name = f"user_{user_id}_avatar.png"
      blob = bucket.blob(blob_name)
      
      # Download file content
      file_content = blob.download_as_bytes()
      
      return send_file(
          BytesIO(file_content),
          mimetype='image/png',
          as_attachment=False,
          download_name=f'avatar.png'
      )
  
  except Exception as e:
      print(f"Failed to retrieve avatar: {e}")
      return jsonify({"Error": "Failed to retrieve avatar"}), 500

# ============================================================================
# ENDPOINT 6: DELETE /users/:user_id/avatar
# ============================================================================

@app.route('/users/<int:user_id>/avatar', methods=['DELETE'])
def delete_avatar(user_id):
  """Delete avatar for a user - self only"""
  try:
      current_user = get_user_from_jwt(request)
  except AuthError as e:
      return jsonify(e.error), e.status_code

  # Check permission - ONLY the user themselves (no admin override)
  if current_user.key.id != user_id:
      return jsonify(ERROR_403), 403

  user_key = client.key(USERS, user_id)
  user = client.get(key=user_key)
  if not user:
      return jsonify(ERROR_404), 404

  if "avatar_url" not in user and "avatar_blob" not in user:
      return jsonify(ERROR_404), 404

  try:
      bucket = storage_client.get_bucket(BUCKET_NAME)

      # Determine candidate blob names (try avatar_blob, then any GCS URL, then canonical fallback)
      candidates = []
      if "avatar_blob" in user:
          candidates.append(user["avatar_blob"])

      avatar_url = user.get("avatar_url", "")
      if avatar_url:
          if "storage.googleapis.com" in avatar_url or BUCKET_NAME in avatar_url:
              candidates.append(avatar_url.split("/")[-1])
          elif avatar_url.rstrip("/").endswith(f"/users/{user_id}/avatar"):
              # fallback canonical name
              candidates.append(f"user_{user_id}_avatar.png")

      # Always include canonical fallback as last resort
      candidates.append(f"user_{user_id}_avatar.png")

      # Deduplicate while preserving order
      seen = set()
      candidates = [c for c in candidates if not (c in seen or seen.add(c))]

      # Try deleting each candidate; ignore NotFound errors
      for blob_name in candidates:
          try:
              blob = bucket.blob(blob_name)
              blob.delete()
          except Exception:
              pass

      # Remove avatar metadata fields unconditionally and persist
      removed = False
      if "avatar_url" in user:
          del user["avatar_url"]
          removed = True
      if "avatar_blob" in user:
          del user["avatar_blob"]
          removed = True
      if removed:
          client.put(user)

      return '', 204

  except Exception:
      return jsonify({"Error": "Failed to delete avatar"}), 500

# ============================================================================
# ENDPOINT 7: POST /courses
# ============================================================================

@app.route('/courses', methods=['POST'])
def create_course():
  """Create a course - admin only"""
  try:
      current_user = get_user_from_jwt(request)
  except AuthError as e:
      return jsonify(e.error), e.status_code

  # Only admins can create courses
  if current_user["role"] != "admin":
      return jsonify(ERROR_403), 403

  content = request.get_json(silent=True)
  if not content:
      return jsonify(ERROR_400), 400

  required_fields = ["subject", "number", "title", "term", "instructor_id"]
  if not all(field in content for field in required_fields):
      return jsonify(ERROR_400), 400

  # Ensure instructor_id is an integer
  try:
      instructor_id = int(content["instructor_id"])
  except (ValueError, TypeError):
      return jsonify(ERROR_400), 400

  # Verify instructor exists and has instructor role
  instructor_key = client.key(USERS, instructor_id)
  instructor = client.get(key=instructor_key)

  if not instructor or instructor.get("role") != "instructor":
      return jsonify(ERROR_400), 400

  # Create course
  new_course = datastore.Entity(key=client.key(COURSES))
  new_course.update({
      "subject": content["subject"],
      "number": content["number"],
      "title": content["title"],
      "term": content["term"],
      "instructor_id": instructor_id,
      "students": []
  })
  client.put(new_course)

  return jsonify({
      "id": new_course.key.id,
      "subject": new_course["subject"],
      "number": new_course["number"],
      "title": new_course["title"],
      "term": new_course["term"],
      "instructor_id": new_course["instructor_id"],
      "self": f"{request.url_root}courses/{new_course.key.id}"
  }), 201

# ============================================================================
# ENDPOINT 8: GET /courses
# ============================================================================

@app.route('/courses', methods=['GET'])
def get_courses():
  """Get all courses with pagination"""
  query = client.query(kind=COURSES)
  query.order = ['subject']  # Sort by subject
  
  # Pagination
  limit = 3
  offset = int(request.args.get('offset', 0))
  
  # Get total count
  all_courses = list(query.fetch())
  total = len(all_courses)
  
  # Get page
  query = client.query(kind=COURSES)
  query.order = ['subject']  # Sort by subject
  iterator = query.fetch(limit=limit, offset=offset)
  page = next(iterator.pages)
  courses = list(page)
  
  courses_list = []
  for course in courses:
      courses_list.append({
          "id": course.key.id,
          "subject": course["subject"],
          "number": course["number"],
          "title": course["title"],
          "term": course["term"],
          "instructor_id": course["instructor_id"],
          "self": f"{request.url_root}courses/{course.key.id}"
      })
  
  result = {"courses": courses_list}
  
  # Add next link if more results
  if offset + limit < total:
      result["next"] = f"{request.base_url}?offset={offset + limit}&limit={limit}"
  
  return jsonify(result), 200
# ============================================================================
# ENDPOINT 9: GET /courses/:course_id
# ============================================================================

@app.route('/courses/<int:course_id>', methods=['GET'])
def get_course(course_id):
  """Get a single course"""
  course_key = client.key(COURSES, course_id)
  course = client.get(key=course_key)
  
  if not course:
      return jsonify({"Error": "Not found"}), 404
  
  return jsonify({
      "id": course.key.id,
      "subject": course["subject"],
      "number": course["number"],
      "title": course["title"],
      "term": course["term"],
      "instructor_id": course["instructor_id"],
      "students": course.get("students", []),
      "self": f"{request.url_root}courses/{course.key.id}"
  }), 200
# ============================================================================
# ENDPOINT 10: PATCH /courses/:course_id
# ============================================================================

@app.route('/courses/<int:course_id>', methods=['PATCH'])
def update_course(course_id):
  """Update a course - admin or assigned instructor only"""
  try:
      current_user = get_user_from_jwt(request)
  except AuthError as e:
      return jsonify(e.error), e.status_code
  
  course_key = client.key(COURSES, course_id)
  course = client.get(key=course_key)
  
  if not course:
      return jsonify({"Error": "Not found"}), 404
  
  # Check permission - must be admin or the assigned instructor
  if current_user["role"] != "admin" and course["instructor_id"] != current_user.key.id:
      return jsonify({"Error": "You don't have permission on this resource"}), 403
  
  content = request.get_json(silent=True)
  if not content:
      return jsonify({"Error": "The request body is invalid"}), 400
  
  # Validate instructor_id if provided
  if "instructor_id" in content:
      instructor_key = client.key(USERS, content["instructor_id"])
      instructor = client.get(key=instructor_key)
      if not instructor or instructor["role"] != "instructor":
          return jsonify({"Error": "The request body is invalid"}), 400
      course["instructor_id"] = content["instructor_id"]
  
  # Update allowed fields
  for field in ["subject", "number", "title", "term"]:
      if field in content:
          course[field] = content[field]
  
  client.put(course)
  
  return jsonify({
      "id": course.key.id,
      "subject": course["subject"],
      "number": course["number"],
      "title": course["title"],
      "term": course["term"],
      "instructor_id": course["instructor_id"]
  }), 200

# ============================================================================
# ENDPOINT 11: DELETE /courses/:course_id
# ============================================================================

@app.route('/courses/<int:course_id>', methods=['DELETE'])
def delete_course(course_id):
  """Delete a course - admin only"""
  try:
      current_user = get_user_from_jwt(request)
  except AuthError as e:
      return jsonify(e.error), e.status_code
  
  if current_user["role"] != "admin":
      return jsonify(ERROR_403), 403
  
  course_key = client.key(COURSES, course_id)
  course = client.get(key=course_key)
  
  # Return 403 for non-existent course - admin-only endpoint
  if not course:
      return jsonify(ERROR_403), 403
  
  # Delete all assignments for this course
  query = client.query(kind=ASSIGNMENTS)
  query.add_filter(filter=("course_id", "=", course_id))
  assignments = list(query.fetch())
  for assignment in assignments:
      client.delete(assignment.key)
  
  client.delete(course_key)
  return '', 204

# ============================================================================
# ENDPOINT 12: PATCH /courses/:course_id/students
# ============================================================================

@app.route('/courses/<int:course_id>/students', methods=['PATCH'])
def update_enrollment(course_id):
  """Update student enrollment - admin or assigned instructor only"""
  try:
      current_user = get_user_from_jwt(request)
  except AuthError as e:
      return jsonify(e.error), e.status_code
  
  course_key = client.key(COURSES, course_id)
  course = client.get(key=course_key)
  
  if not course:
      return jsonify(ERROR_403), 403
  
  # Check permission - admin OR the assigned instructor
  if current_user["role"] != "admin" and course["instructor_id"] != current_user.key.id:
      return jsonify(ERROR_403), 403
  
  content = request.get_json(silent=True)
  if not content or "add" not in content or "remove" not in content:
      return jsonify(ERROR_400), 400
  
  add_students = content["add"]
  remove_students = content["remove"]
  
  # Check for overlapping students in add and remove arrays
  add_set = set(add_students)
  remove_set = set(remove_students)
  if add_set & remove_set:
      return jsonify({"Error": "Enrollment data is invalid"}), 409
  
  # Validate all student IDs exist and have student role
  all_student_ids = add_set | remove_set
  for student_id in all_student_ids:
      student_key = client.key(USERS, student_id)
      student = client.get(key=student_key)
      if not student or student["role"] != "student":
          return jsonify({"Error": "Enrollment data is invalid"}), 409
  
  # Update enrollment
  current_students = set(course.get("students", []))
  
  # Add new students
  current_students.update(add_students)
  
  # Remove students
  current_students.difference_update(remove_students)
  
  course["students"] = list(current_students)
  client.put(course)
  
  return '', 200

# ============================================================================
# ENDPOINT 13: GET /courses/:course_id/students
# ============================================================================

@app.route('/courses/<int:course_id>/students', methods=['GET'])
def get_course_students(course_id):
  """Get enrolled students - admin or assigned instructor only"""
  try:
      current_user = get_user_from_jwt(request)
  except AuthError as e:
      return jsonify(e.error), e.status_code
  
  course_key = client.key(COURSES, course_id)
  course = client.get(key=course_key)
  
  # Return 403 for non-existent course 
  if not course:
      return jsonify(ERROR_403), 403
  
  # Check permission - must be admin OR the assigned instructor for THIS course
  if current_user["role"] != "admin" and course["instructor_id"] != current_user.key.id:
      return jsonify(ERROR_403), 403
  
  return jsonify({"students": course.get("students", [])}), 200


# ============================================================================
# ROOT ENDPOINT
# ============================================================================

@app.route('/', methods=['GET'])
def index():
  return jsonify({"message": "Tarpaulin API - Use /users/login to get started"}), 200

if __name__ == "__main__":
  app.run(host="127.0.0.1", port=8080, debug=True)