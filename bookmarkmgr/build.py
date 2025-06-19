# ruff: noqa: PGH003
# type: ignore

import re
import subprocess

from cffi import FFI

CRONET_INCLUDE = "#include <cronet_c.h>"

UNDEFINED_SYMBOLS = [
    "Cronet_Metrics_connect_end_move",
    "Cronet_Metrics_connect_start_move",
    "Cronet_Metrics_dns_end_move",
    "Cronet_Metrics_dns_start_move",
    "Cronet_Metrics_push_end_move",
    "Cronet_Metrics_push_start_move",
    "Cronet_Metrics_request_end_move",
    "Cronet_Metrics_request_start_move",
    "Cronet_Metrics_response_start_move",
    "Cronet_Metrics_sending_end_move",
    "Cronet_Metrics_sending_start_move",
    "Cronet_Metrics_ssl_end_move",
    "Cronet_Metrics_ssl_start_move",
    "Cronet_RequestFinishedInfo_metrics_move",
    "Cronet_UploadDataSink_Create",
]

ffibuilder = FFI()
ffibuilder.cdef(
    re.sub(
        rf"^.+? ({'|'.join(UNDEFINED_SYMBOLS)})\([\S\s]+?\);$",
        "",
        subprocess.run(
            [
                "cpp",
                "-DCOMPONENTS_CRONET_NATIVE_INCLUDE_CRONET_EXPORT_H_",
                "-DCRONET_EXPORT=",
                "-P",
            ],
            check=True,
            input=CRONET_INCLUDE,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout,
        flags=re.MULTILINE,
    ),
)
ffibuilder.cdef(
    """
    extern "Python" void _executor_execute(
        Cronet_ExecutorPtr,
        Cronet_RunnablePtr
    );

    extern "Python" void _on_request_redirect_received(
        Cronet_UrlRequestCallbackPtr,
        Cronet_UrlRequestPtr,
        Cronet_UrlResponseInfoPtr,
        Cronet_String
    );
    extern "Python" void _on_request_response_started(
        Cronet_UrlRequestCallbackPtr,
        Cronet_UrlRequestPtr,
        Cronet_UrlResponseInfoPtr
    );
    extern "Python" void _on_request_read_completed(
        Cronet_UrlRequestCallbackPtr,
        Cronet_UrlRequestPtr,
        Cronet_UrlResponseInfoPtr,
        Cronet_BufferPtr,
        uint64_t
    );
    extern "Python" void _on_request_succeeded(
        Cronet_UrlRequestCallbackPtr,
        Cronet_UrlRequestPtr,
        Cronet_UrlResponseInfoPtr
    );
    extern "Python" void _on_request_failed(
        Cronet_UrlRequestCallbackPtr,
        Cronet_UrlRequestPtr,
        Cronet_UrlResponseInfoPtr,
        Cronet_ErrorPtr
    );
    extern "Python" void _on_request_canceled(
        Cronet_UrlRequestCallbackPtr,
        Cronet_UrlRequestPtr,
        Cronet_UrlResponseInfoPtr
    );
    """,
)
ffibuilder.set_source(
    "bookmarkmgr.cronet._cronet",
    f"#include <stdbool.h>\n{CRONET_INCLUDE}",
    libraries=["cronet"],
)

if __name__ == "__main__":
    ffibuilder.compile(verbose=True)
