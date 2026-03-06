import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

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
# POST /create_user
# -------------------------------------------------------
@app.route("/create_user", methods=["POST"])
def create_user():
    data = request.json
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        sql = """
            INSERT INTO Users (name, nickname, email, pronouns, graduation_year, minibio)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING userID;
        """
        cur.execute(sql, (
            data['name'], data['nickname'], data['email'],
            data.get('pronouns'), data.get('graduation_year'), data.get('minibio')
        ))
        new_id = cur.fetchone()['userid']
        conn.commit()
        return jsonify({"userID": new_id, "status": "Success"}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        cur.close()
        conn.close()


# -------------------------------------------------------
# ENDPOINT 2: CONNECT TWO USERS
# POST /connect_users
# -------------------------------------------------------
@app.route("/connect_users", methods=["POST"])
def connect_users():
    data = request.json
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            "INSERT INTO Connections (userID1, userID2) VALUES (%s, %s) RETURNING connectionID;",
            (data['user1'], data['user2'])
        )
        conn_id = cur.fetchone()['connectionid']
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
# GET /get_my_connections?userID=5
# -------------------------------------------------------
@app.route("/get_my_connections", methods=["GET"])
def get_my_connections():
    user_id = request.args.get('userID')
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        sql = """
            SELECT u.userID, u.name, u.minibio
            FROM Users u
            JOIN Connections c ON (u.userID = c.userID1 OR u.userID = c.userID2)
            WHERE (c.userID1 = %s OR c.userID2 = %s) AND u.userID != %s;
        """
        cur.execute(sql, (user_id, user_id, user_id))
        results = cur.fetchall()
        return jsonify(results), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        cur.close()
        conn.close()


# -------------------------------------------------------
# ENDPOINT 4: GET A SINGLE USER'S PROFILE
# GET /get_user?userID=5
# Returns: userID, name, minibio
# -------------------------------------------------------
@app.route("/get_user", methods=["GET"])
def get_user():
    user_id = request.args.get('userID')
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            "SELECT userID, name, minibio FROM Users WHERE userID = %s;",
            (user_id,)
        )
        user = cur.fetchone()
        if not user:
            return jsonify({"error": "User not found"}), 404
        return jsonify(user), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        cur.close()
        conn.close()


# -------------------------------------------------------
# ENDPOINT 5: UPDATE A USER'S NAME AND MINIBIO
# POST /update_user
# Body: { "userID": 1, "name": "New Name", "minibio": "New bio" }
# -------------------------------------------------------
@app.route("/update_user", methods=["POST"])
def update_user():
    data = request.json
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            """
            UPDATE Users 
            SET name = %s, minibio = %s 
            WHERE userID = %s 
            RETURNING userID, name, minibio;
            """,
            (data['name'], data.get('minibio'), data['userID'])
        )
        updated = cur.fetchone()
        if not updated:
            return jsonify({"error": "User not found"}), 404
        conn.commit()
        return jsonify({"status": "Success", "user": updated}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        cur.close()
        conn.close()


# -------------------------------------------------------
# ENDPOINT 6: DELETE A CONNECTION
# POST /delete_connection
# Body: { "connectionID": 1 }
# -------------------------------------------------------
@app.route("/delete_connection", methods=["POST"])
def delete_connection():
    data = request.json
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # Delete Results rows first (they reference connectionID)
        cur.execute("DELETE FROM Results WHERE connectionID = %s;", (data['connectionID'],))
        cur.execute("DELETE FROM Connections WHERE connectionID = %s;", (data['connectionID'],))
        conn.commit()
        return jsonify({"status": "Connection deleted"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        cur.close()
        conn.close()


# -------------------------------------------------------
# ENDPOINT 7: UPDATE A RESULT SCORE AND NOTE
# POST /update_result
# Body: { "userID": 1, "connectionID": 2, "score": 8, "note": "Great convo!" }
# -------------------------------------------------------
@app.route("/update_result", methods=["POST"])
def update_result():
    data = request.json
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            """
            UPDATE Results 
            SET score = %s, note = %s 
            WHERE userID = %s AND connectionID = %s
            RETURNING *;
            """,
            (data.get('score'), data.get('note'), data['userID'], data['connectionID'])
        )
        updated = cur.fetchone()
        if not updated:
            return jsonify({"error": "Result not found"}), 404
        conn.commit()
        return jsonify({"status": "Success", "result": updated}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        cur.close()
        conn.close()


# -------------------------------------------------------
# ENDPOINT 8: SEARCH USERS BY NAME OR NICKNAME
# GET /search_users?query=chris
# -------------------------------------------------------
@app.route("/search_users", methods=["GET"])
def search_users():
    query = request.args.get('query')
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            """
            SELECT userID, name, nickname, minibio 
            FROM Users 
            WHERE name ILIKE %s OR nickname ILIKE %s;
            """,
            (f'%{query}%', f'%{query}%')
        )
        results = cur.fetchall()
        return jsonify(results), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    finally:
        cur.close()
        conn.close()


@app.route("/")
def health():
    return jsonify({"status": "alive"}), 200

if __name__ == "__main__":
    app.run(debug=True)