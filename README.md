# Tarpaulin — Course Management REST API

A full course-management REST API built for CS 493 (Cloud Application Development) at Oregon State University. Deployed on Google App Engine with Auth0 JWT authentication, Google Cloud Datastore for structured data, and Google Cloud Storage for user avatar uploads.

114/114 automated Postman tests passing across auth flows, role-based access control, and full CRUD.

## Stack

- **Flask** — REST API framework
- **Auth0** — JWT-based authentication (RS256), role-based access control (admin / instructor / student)
- **Google Cloud Datastore** — NoSQL backend for users, courses, and enrollments
- **Google Cloud Storage** — avatar image uploads
- **Google App Engine** — deployment target (Python 3.11 runtime)

## Endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/users/login` | Authenticate and receive a JWT |
| GET | `/users` | List all users (admin only) |
| GET | `/users/<id>` | Get a user's details |
| POST | `/users/<id>/avatar` | Upload a user's avatar |
| GET | `/users/<id>/avatar` | Download a user's avatar |
| DELETE | `/users/<id>/avatar` | Delete a user's avatar |
| POST | `/courses` | Create a course (admin only) |
| GET | `/courses` | List courses |
| GET | `/courses/<id>` | Get a course's details |
| PATCH | `/courses/<id>` | Update a course (admin only) |
| DELETE | `/courses/<id>` | Delete a course (admin only) |
| PATCH | `/courses/<id>/students` | Update course enrollment |
| GET | `/courses/<id>/students` | List a course's enrolled students |

Access control is role-based: admins manage users and courses, instructors manage their own courses and view rosters, and students can view their own enrollment info.

## Running it yourself

1. Copy `app.yaml.example` to `app.yaml` and fill in your own Auth0 tenant details and GCS bucket name:
   ```bash
   cp app.yaml.example app.yaml
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Deploy to App Engine:
   ```bash
   gcloud app deploy
   ```

`app.yaml` is gitignored since it holds real credentials once filled in — never commit it with real values.

## Notes

Avatar images are stored in Google Cloud Storage and served back through signed download URLs. JWTs are verified against Auth0's JWKS endpoint on every protected request (RS256, no local secret verification).
