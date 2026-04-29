import os

class Config:
    # ── Core ──────────────────────────────────────────────────────────
    # Never hardcode this. Set it in your shell or .env before running.
    # e.g.  export SECRET_KEY="some-long-random-string"
    SECRET_KEY = os.environ.get('SECRET_KEY', 'change-me-before-production')

    # ── Database ──────────────────────────────────────────────────────
    # e.g.  export DATABASE_URL="mysql+pymysql://user:pass@localhost/lumiere"
##    SQLALCHEMY_DATABASE_URI = os.environ.get(
##        'DATABASE_URL',
##        'mysql+pymysql://root:@localhost/salon_db'   
##    )
    
    SQLALCHEMY_DATABASE_URI = os.environ.get(
    'DATABASE_URL',
    'sqlite:///salon.db'
    )
    
    
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ── Admin credentials ─────────────────────────────────────────────
    # e.g.  export ADMIN_USERNAME="admin"
    #       export ADMIN_PASSWORD="your-strong-password"
    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'lumiere@2025')

    # ── CSRF (Flask-WTF) ──────────────────────────────────────────────
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600   # token valid for 1 hour