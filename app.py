import os
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

# 1. CREATE USER
# Args: JSON body {name, nickname, caseID, (optional: pronouns, graduation_year, hometown, nationality, image_link (Null?), pronunciation, minibio, fact, is_public_leaderboard)}
# Returns: JSON {userID, status}
@app.route("/create_user", methods=["POST"])
def create_user():
    data = request.json
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        sql = """
            INSERT INTO Users (name, nickname, caseID, pronouns, graduation_year, hometown, nationality, image_link, pronunciation, minibio, fact, is_public_leaderboard)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING userID;
        """
        cur.execute(sql, (
            data['name'], data['nickname'], data['caseID'].lower(), data.get('pronouns'),
            data.get('graduation_year'), data.get('hometown'), data.get('nationality'),
            data.get('image_link'), data.get('pronunciation'), data.get('minibio'), 
            data.get('fact'), data.get('is_public_leaderboard', True)
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

# 2. GET CONNECTIONS
# Args: user_id (int) as URL path parameter
# Returns: JSON list of objects [{userID, name, nickname, image_link, caseID, note, starred, matched_at}, ...]
@app.route("/get_connections/<int:user_id>", methods=["GET"])
def get_connections(user_id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        sql = """
            SELECT u.userID, u.name, u.nickname, u.image_link, u.caseID, c.note, c.starred, c.matched_at
            FROM Connections c
            JOIN Users u ON c.friendID = u.userID
            WHERE c.userID = %s
            ORDER BY c.starred DESC, u.name ASC;
        """
        cur.execute(sql, (user_id,))
        return jsonify(cur.fetchall()), 200
    finally:
        cur.close()
        conn.close()

# 3. ADD CONNECTION
# Args: JSON body {userID, targetID}
# Returns: JSON {status, points_earned}
# Note: Now increments successful_reconnections in the Users table and deletes the suggestion record
@app.route("/add_connection", methods=["POST"])
def add_connection():
    data = request.json
    u1, u2 = data['userID'], data['targetID']
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT 1 FROM Connections WHERE userID = %s AND friendID = %s", (u1, u2))
        exists = cur.fetchone()

        cur.execute("""
            SELECT is_reconnection FROM WeeklyProfileSuggestions 
            WHERE userID = %s AND suggestedUserID = %s AND matched = FALSE
        """, (u1, u2))
        bonus_eligible = cur.fetchone()

        if exists and not bonus_eligible:
            return jsonify({"message": "You have already met this user recently."}), 200

        # Point Logic
        points_to_add = 10 if bonus_eligible else 5
        is_bonus_recon = bonus_eligible and bonus_eligible['is_reconnection']
        reason = 'reconnection_bonus' if is_bonus_recon else ('new_connection_bonus' if bonus_eligible else 'new_connection')

        # Create/Update Connections (Symmetric)
        cur.execute("INSERT INTO Connections (userID, friendID) VALUES (%s, %s) ON CONFLICT DO NOTHING", (u1, u2))
        cur.execute("INSERT INTO Connections (userID, friendID) VALUES (%s, %s) ON CONFLICT DO NOTHING", (u2, u1))

        # Log Points
        cur.execute("INSERT INTO MatchPointLog (userID, friendID, points, reason) VALUES (%s, %s, %s, %s)", (u1, u2, points_to_add, reason))
        cur.execute("INSERT INTO MatchPointLog (userID, friendID, points, reason) VALUES (%s, %s, %s, %s)", (u2, u1, points_to_add, reason))

        # NEW LOGIC: Increment User variable and clean up suggestion
        if bonus_eligible:
            if is_bonus_recon:
                cur.execute("UPDATE Users SET successful_reconnections = successful_reconnections + 1 WHERE userID IN (%s, %s)", (u1, u2))
            
            # Remove the suggestion so it can't be reused and doesn't bloat the DB
            cur.execute("DELETE FROM WeeklyProfileSuggestions WHERE (userID = %s AND suggestedUserID = %s) OR (userID = %s AND suggestedUserID = %s)", (u1, u2, u2, u1))

        conn.commit()
        return jsonify({"status": "Success", "points_earned": points_to_add}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        cur.close()
        conn.close()

# 4. REMOVE CONNECTION
# Args: JSON body {userID, friendID}
# Returns: JSON {status}
@app.route("/remove_connection", methods=["POST"])
def remove_connection():
    data = request.json
    u1, u2 = data['userID'], data['friendID']
    conn = get_db()
    cur = conn.cursor()
    try:
        # Delete symmetric rows from all tables
        cur.execute("DELETE FROM Connections WHERE (userID = %s AND friendID = %s) OR (userID = %s AND friendID = %s)", (u1, u2, u2, u1))
        cur.execute("DELETE FROM MatchPointLog WHERE (userID = %s AND friendID = %s) OR (userID = %s AND friendID = %s)", (u1, u2, u2, u1))
        cur.execute("DELETE FROM StudyScoreLog WHERE (userID = %s AND friendID = %s) OR (userID = %s AND friendID = %s)", (u1, u2, u2, u1))
        conn.commit()
        return jsonify({"status": "Connection removed"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        cur.close()
        conn.close()

# 5. GET USER STATS
# Args: user_id (int) as URL path parameter
# Returns: JSON object containing all columns from Users + calculated Monthly/Yearly/All-time points and connection/reconnection counts
@app.route("/get_user_stats/<int:user_id>", methods=["GET"])
def get_user_stats(user_id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("SELECT * FROM UserProfile_Stats WHERE userID = %s", (user_id,))
        stats = cur.fetchone()
        return jsonify(stats) if stats else (jsonify({"error": "Not found"}), 404)
    finally:
        cur.close()
        conn.close()

# 6. UPDATE USER
# Args: JSON body containing 'userID' and any fields to update (e.g., {userID: 1, minibio: "new bio", graduation_year: 2027})
# Returns: JSON {status}
@app.route("/update_user", methods=["PUT"])
def update_user():
    data = request.json
    user_id = data.pop('userID', None)
    if not user_id:
        return jsonify({"error": "userID required"}), 400
    conn = get_db()
    cur = conn.cursor()
    try:
        keys = data.keys()
        set_clause = ", ".join([f"{k} = %s" for k in keys])
        values = list(data.values())
        values.append(user_id)
        cur.execute(f"UPDATE Users SET {set_clause} WHERE userID = %s", values)
        conn.commit()
        return jsonify({"status": "Updated"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        cur.close()
        conn.close()

# 7. GET LEADERBOARD
# Args: category (string: match/study), timeframe (string: monthly/yearly/all_time). Query Param: userID (int), friends_only (bool)
# Returns: JSON {top_25: [], user_rank: int}
@app.route("/leaderboard/<string:category>/<string:timeframe>", methods=["GET"])
def get_leaderboard(category, timeframe):
    user_id = request.args.get('userID')
    friends_only = request.args.get('friends_only', 'false').lower() == 'true'
    view_name = f"{category.capitalize()}_{timeframe.capitalize()}"
    
    if timeframe == 'all_time':
        score_col = f"{category}_points_all_time"
        table_source = "Users"
    else:
        score_col = "points"
        table_source = view_name

    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # Build Filter for friends_only
        filter_clause = "WHERE u.is_public_leaderboard = TRUE"
        if friends_only and user_id:
            filter_clause += f" AND (u.userID = {user_id} OR u.userID IN (SELECT friendID FROM Connections WHERE userID = {user_id}))"

        # 1. Get List
        cur.execute(f"""
            SELECT u.name, u.image_link, s.{score_col} as score
            FROM {table_source} s
            JOIN Users u ON s.userID = u.userID
            {filter_clause}
            ORDER BY score DESC LIMIT 25
        """)
        top_list = cur.fetchall()

        # 2. Get User Rank (Ranked against the selected scope)
        cur.execute(f"""
            SELECT rank FROM (
                SELECT s.userID, RANK() OVER (ORDER BY s.{score_col} DESC) as rank
                FROM {table_source} s
                JOIN Users u ON s.userID = u.userID
                {filter_clause}
            ) as ranks WHERE userID = %s
        """, (user_id,))
        user_rank = cur.fetchone()

        return jsonify({"top_list": top_list, "user_rank": user_rank['rank'] if user_rank else None})
    finally:
        cur.close()
        conn.close()

# 8. DELETE ACCOUNT
# Args: user_id (int) as URL path parameter
# Returns: JSON {status}
@app.route("/delete_account/<int:user_id>", methods=["DELETE"])
def delete_account(user_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM Users WHERE userID = %s", (user_id,))
        conn.commit()
        return jsonify({"status": "Account deleted"}), 200
    finally:
        cur.close()
        conn.close()

# 9. TOGGLE STAR
# Args: JSON body {userID, friendID}
# Returns: JSON {status, is_starred}
@app.route("/toggle_star", methods=["POST"])
def toggle_star():
    data = request.json
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute("""
            UPDATE Connections 
            SET starred = NOT starred 
            WHERE userID = %s AND friendID = %s 
            RETURNING starred;
        """, (data['userID'], data['friendID']))
        new_state = cur.fetchone()
        conn.commit()
        return jsonify({"status": "Success", "is_starred": new_state['starred']}), 200
    finally:
        cur.close()
        conn.close()

# 10. GET WEEKLY TARGETS
# Args: user_id (int) as path parameter
# Returns: List of Users that are current suggestions for the week
@app.route("/get_weekly_targets/<int:user_id>", methods=["GET"])
def get_weekly_targets(user_id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        sql = """
            SELECT u.userID, u.name, u.nickname, u.image_link, w.is_reconnection
            FROM WeeklyProfileSuggestions w
            JOIN Users u ON w.suggestedUserID = u.userID
            WHERE w.userID = %s AND w.matched = FALSE;
        """
        cur.execute(sql, (user_id,))
        return jsonify(cur.fetchall()), 200
    finally:
        cur.close()
        conn.close()

# 11. RECORD STUDY GAME
# Args: userID (int), targetID (int), was_correct (bool)
# Returns: JSON {status, new_score, best_score, points_earned}
@app.route("/record_study_game", methods=["POST"])
def record_study_game():
    data = request.json
    u1, u2 = data['userID'], data['targetID']
    was_correct = data.get('was_correct', False)
    
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        # 1. Get current standing
        cur.execute("SELECT score, best_score FROM Connections WHERE userID = %s AND friendID = %s", (u1, u2))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Connection not found"}), 404
        
        current_score = row['score']
        best_score = row['best_score']
        points_to_add = 0
        
        if was_correct:
            if current_score < 10:
                # FAST RECOVERY: If they've hit this peak before, give them +2 to get back there faster
                # Otherwise, just give them +1 for new progress
                gain = 2 if current_score < best_score else 1
                new_score = min(10, current_score + gain)
                points_to_add = gain
                
                # Update best_score if they reached a new peak
                if new_score > best_score:
                    best_score = new_score
            else:
                new_score = 10 # Already mastered
        else:
            # Decrease score by 1 if wrong (floor of 0)
            new_score = max(0, current_score - 1)
            points_to_add = 0

        # 2. Update the connection table
        cur.execute("""
            UPDATE Connections 
            SET score = %s, best_score = %s 
            WHERE userID = %s AND friendID = %s
        """, (new_score, best_score, u1, u2))

        # 3. Log Study Points for Leaderboard (ONLY if they got it right)
        if points_to_add > 0:
            cur.execute("""
                INSERT INTO StudyScoreLog (userID, friendID, score_change) 
                VALUES (%s, %s, %s)
            """, (u1, u2, points_to_add))
        
        conn.commit()
        return jsonify({
            "status": "Success", 
            "new_score": new_score, 
            "best_score": best_score,
            "points_earned": points_to_add
        }), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        cur.close()
        conn.close()

# 12. GET ALL MAJORS
# Returns: List of all majors for dropdown menus in the UI
@app.route("/majors", methods=["GET"])
def get_majors():
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM Majors ORDER BY major ASC;")
    majors = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(majors), 200

# 13. SET USER MAJORS
# Args: JSON {userID: int, majorIDs: [int, int]}
# Replaces current majors with a new selection
@app.route("/set_user_majors", methods=["POST"])
def set_user_majors():
    data = request.json
    conn = get_db()
    cur = conn.cursor()
    try:
        # Clear existing
        cur.execute("DELETE FROM MajorConnection WHERE userID = %s", (data['userID'],))
        # Insert new
        for m_id in data['majorIDs']:
            cur.execute("INSERT INTO MajorConnection (userID, majorID) VALUES (%s, %s)", (data['userID'], m_id))
        conn.commit()
        return jsonify({"status": "Success"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 400
    finally:
        cur.close()
        conn.close()

# 14. GET GAME DECK
# Args: user_id (int) as path param
# Returns: 10 friends with images, prioritizing those with scores < 10
@app.route("/get_game_deck/<int:user_id>", methods=["GET"])
def get_game_deck(user_id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # We add "u.image_link IS NOT NULL" to ensure the game is playable
    sql = """
        SELECT u.userID, u.nickname, u.image_link, c.score, c.best_score
        FROM Connections c
        JOIN Users u ON c.friendID = u.userID
        WHERE c.userID = %s 
          AND u.image_link IS NOT NULL 
          AND u.image_link <> ''
        ORDER BY (c.score < 10) DESC, RANDOM()
        LIMIT 10;
    """
    cur.execute(sql, (user_id,))
    deck = cur.fetchall()
    cur.close()
    conn.close()
    
    if not deck:
        return jsonify({"message": "Add more friends with profile pictures to play!"}), 200
        
    return jsonify(deck), 200

if __name__ == "__main__":
    app.run(debug=True)