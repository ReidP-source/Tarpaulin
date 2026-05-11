# Tarpaulin Course Management API

A Flask REST API for a course management system inspired by Tarpaulin, a fictional Canvas-like education platform.

The API supports Auth0 authentication, role-based access control, course management, student enrollment, and avatar uploads backed by Google Cloud Datastore and Google Cloud Storage.

## Features

- Auth0 login with JWT-based authentication
- Role-based authorization for admins, instructors, and students
- User profile lookup with course membership data
- Avatar upload, retrieval, and deletion through Google Cloud Storage
- Course creation, listing, lookup, update, and deletion
- Student enrollment management
- Google Cloud Datastore persistence
- Startup seeding for default users from `users.json`

## Tech Stack

- Python
- Flask
- Auth0
- Google Cloud Datastore
- Google Cloud Storage
- `python-jose` for JWT verification
- `python-dotenv` for local environment configuration

## Project Structure

```text
.
├── main.py
├── users.json
├── .env (ignored)
├── app.yaml
├── requirements.txt
└── README.md
```

`main.py` contains the Flask application and route handlers. `users.json` contains the seed users used to initialize Auth0 and Datastore.

## Environment Variables

Create a `.env` file in the project root:

```env
AUTH0_CLIENT_ID=your-auth0-client-id
AUTH0_CLIENT_SECRET=your-auth0-client-secret
AUTH0_DOMAIN=your-auth0-domain
APP_SECRET_KEY=your-flask-secret-key
```

The application expects these Auth0 values on startup. If any are missing, the app exits with an error.

## Google Cloud Configuration

The app is configured for the following Google Cloud project:

```python
PROJECT_ID = e.g."assignment6-pettibor"
BUCKET_NAME = e.g."assignment6-pettibor-avatars"
```

Before running the app, make sure:

- Google Cloud Datastore is enabled for the project.
- A Cloud Storage bucket named `assignment6-pettibor-avatars` exists.
- Your local environment or deployment environment has Google Cloud credentials configured.
- The service account has access to Datastore and Cloud Storage.

For local development, authenticate with:

```bash
gcloud auth application-default login
```

## Installation

Install the required Python packages:

```bash
pip install Flask google-cloud-datastore google-cloud-storage requests python-jose python-dotenv six
```

## Running Locally

Start the Flask server:

```bash
python main.py
```

The API runs locally at:

```text
http://127.0.0.1:8080
```

On startup, the app attempts to seed users from `users.json`. It stores a Datastore marker so seeding only runs once.

## Authentication

Most protected routes require an Auth0 JWT in the `Authorization` header:

```http
Authorization: Bearer <token>
```

Use the login endpoint to get a token:

```http
POST /users/login
```

Example request body:

```json
{
  "username": "user@example.com",
  "password": "password"
}
```

Example response:

```json
{
  "token": "eyJ..."
}
```

## Roles

The API uses three roles:

| Role | Permissions |
| --- | --- |
| `admin` | View all users, create courses, delete courses, manage enrollment |
| `instructor` | View and update assigned courses, manage students in assigned courses |
| `student` | View own profile and enrolled courses |

Seeded user roles are inferred from email addresses:

- Emails containing `admin` become admins.
- Emails containing `instructor` become instructors.
- All other users become students.

## API Endpoints

### Root

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/` | Health/info message |

### Users

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| `POST` | `/users/login` | None | Log in with Auth0 credentials and receive a JWT |
| `GET` | `/users` | Admin | Get all users |
| `GET` | `/users/<user_id>` | Admin or self | Get one user |
| `POST` | `/users/<user_id>/avatar` | Self | Upload avatar image |
| `GET` | `/users/<user_id>/avatar` | Self | Retrieve avatar image |
| `DELETE` | `/users/<user_id>/avatar` | Self | Delete avatar image |

### Courses

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| `POST` | `/courses` | Admin | Create a course |
| `GET` | `/courses` | None | List courses with pagination |
| `GET` | `/courses/<course_id>` | None | Get one course |
| `PATCH` | `/courses/<course_id>` | Admin or assigned instructor | Update a course |
| `DELETE` | `/courses/<course_id>` | Admin | Delete a course and its assignments |
| `PATCH` | `/courses/<course_id>/students` | Admin or assigned instructor | Add or remove enrolled students |
| `GET` | `/courses/<course_id>/students` | Admin or assigned instructor | Get enrolled students |

## Example Requests

### Create a Course

```bash
curl -X POST http://127.0.0.1:8080/courses \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "subject": "CS",
    "number": 493,
    "title": "Cloud Application Development",
    "term": "Fall 2025",
    "instructor_id": 123456789
  }'
```

### List Courses

```bash
curl http://127.0.0.1:8080/courses
```

Courses are returned three at a time and sorted by subject. If more courses exist, the response includes a `next` URL.

### Update Enrollment

```bash
curl -X PATCH http://127.0.0.1:8080/courses/123/students \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "add": [111, 222],
    "remove": [333]
  }'
```

### Upload an Avatar

```bash
curl -X POST http://127.0.0.1:8080/users/123/avatar \
  -H "Authorization: Bearer <token>" \
  -F "file=@avatar.png"
```

## Error Responses

Common error responses:

```json
{ "Error": "Unauthorized" }
```

```json
{ "Error": "You don't have permission on this resource" }
```

```json
{ "Error": "Not found" }
```

```json
{ "Error": "The request body is invalid" }
```

Enrollment conflicts return:

```json
{ "Error": "Enrollment data is invalid" }
```

## Notes

- Avatar uploads are stored as PNG files in Cloud Storage.
- Avatar URLs are served through the Flask API rather than directly exposing the storage object.
- `DELETE /courses/<course_id>` also deletes assignments associated with the course.
- The app verifies JWTs against Auth0 JWKS and rejects unsupported or invalid tokens.
- The `/courses` listing endpoint currently uses a fixed page size of 3.

## License

This project was created for a course assignment. Add a license before publishing if you want others to reuse it.
