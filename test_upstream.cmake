# Porting upstream testsuite
add_custom_target(busybox_upstream_testsuite_config ALL
    COMMAND ${CMAKE_COMMAND} -E copy "${CMAKE_CURRENT_BINARY_DIR}/include/autoconf.h" "${CMAKE_CURRENT_BINARY_DIR}/.config"
)
add_test(NAME test_upstream_testsuite
         COMMAND busybox ash "${BUSYBOX_SOURCE_DIR}/testsuite/runtest"
         WORKING_DIRECTORY "${BUSYBOX_SOURCE_DIR}/testsuite")
set_tests_properties(test_upstream_testsuite PROPERTIES
                     ENVIRONMENT "bindir=${CMAKE_CURRENT_BINARY_DIR};PATH=${CMAKE_CURRENT_BINARY_DIR};LC_ALL=C")
