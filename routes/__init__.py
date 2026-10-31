"""Web routes, split into one Flask blueprint per area of the app."""


def all_blueprints():
    from routes.auth import bp as auth_bp
    from routes.main import bp as main_bp
    from routes.portal import bp as portal_bp

    return [auth_bp, main_bp, portal_bp]
