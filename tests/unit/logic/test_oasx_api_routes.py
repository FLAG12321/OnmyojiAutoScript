def test_oasx_routers_are_registered():
    from module.server.app import app

    # 路由 smoke 测试：确认 OASX 前端依赖的路径已注册。
    paths = {route.path for route in app.routes}

    assert "/logs/{script_name}" in paths
    assert "/logs/{script_name}/stream" in paths
    assert "/logs/errors" in paths
    assert "/stats/{script_name}/dates" in paths
    assert "/stats/{script_name}" in paths
    assert "/stats/{script_name}/stream" in paths
    assert "/config/import" in paths
    assert "/config/export" in paths
    assert "/config/task/import" in paths
    assert "/config/task/export" in paths
    assert "/config/task/copy-json" in paths
