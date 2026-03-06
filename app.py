import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv

# Load the credentials from your .env file
load_dotenv()

app = Flask(__name__)
CORS(app)  # Allows your Android app to talk to this API

# --- DATABASE CONNECTION ---
# This function creates a fresh connection to your RDS database.
# We call it inside each endpoint so connections don't time out.
def get_db():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        port=os.getenv("DB_PORT")
    )


# -------------------------------------------------------
# ENDPOINT 1: CREATE A USER
# Android calls: POST /create_user
# What it does: Adds a new row to your Users table
# -------------------------------------------------------
@app.route("/create_user", methods=["POST"])
def create_user():
    data = request.json  # grab the JSON body sent from Android
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        sql = """
            INSERT INTO Users (name, nickname, email, pronouns, graduation_year, minibio)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING userID;
        """
        cur.execute(sql, (
            data['name'],
            data['nickname'],
            data['email'],
            data.get('pronouns'),        # .get() means optional — won't crash if missing
            data.get('graduation_year'),
            data.get('minibio')
        ))
        new_id = cur.fetchone()['userid']
        conn.commit()  # actually saves the change to the database
        return jsonify({"userID": new_id, "status": "Success"}), 201
    except Exception as e:
        conn.rollback()  # undo anything if something went wrong
        return jsonify({"error": str(e)}), 400
    finally:
        cur.close()
        conn.close()


# -------------------------------------------------------
# ENDPOINT 2: CONNECT TWO USERS
# Android calls: POST /connect_users
# What it does: Creates a Connection row + blank Results
#               rows for both users (just like your original!)
# -------------------------------------------------------
@app.route("/connect_users", methods=["POST"])
def connect_users():
    data = request.json
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # Step 1: Create the connection between the two users
        cur.execute(
            "INSERT INTO Connections (userID1, userID2) VALUES (%s, %s) RETURNING connectionID;",
            (data['user1'], data['user2'])
        )
        conn_id = cur.fetchone()['connectionid']

        # Step 2: Create blank Results rows for both users
        cur.execute(
            "INSERT INTO Results (userID, connectionID) VALUES (%s, %s), (%s, %s);",
            (data['user1'], conn_id, data['user2'], conn_id)
        )
        conn.commit()
        return jsonify({"connectionID": conn_id}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        cur.close()
        conn.close()


# -------------------------------------------------------
# ENDPOINT 3: GET ALL CONNECTIONS FOR A USER
# Android calls: GET /get_my_connections?userID=5
# What it does: Returns everyone connected to that user
#               with their score and note from Results
# -------------------------------------------------------
@app.route("/get_my_connections", methods=["GET"])
def get_my_connections():
    user_id = request.args.get('userID')  # reads ?userID=5 from the URL
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        sql = """
            SELECT u.name, u.nickname, r.score, r.note
            FROM Users u
            JOIN Connections c ON (u.userID = c.userID1 OR u.userID = c.userID2)
            JOIN Results r ON c.connectionID = r.connectionID
            WHERE r.userID = %s AND u.userID != %s;
        """
        cur.execute(sql, (user_id, user_id))
        results = cur.fetchall()
        return jsonify(results), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        cur.close()
        conn.close()


# --- START THE SERVER ---
if __name__ == "__main__":
    app.run(debug=True)