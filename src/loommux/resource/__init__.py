"""Own logical IPython resources independently from MCP transports."""

from loommux.resource.manager import (
    KernelResourceManager,
    ResourceBusyError,
    ResourceManagerError,
    ResourceNotFoundError,
    ResourceProvisionError,
)
from loommux.resource.model import (
    ClientLease,
    KernelResource,
    LeaseClient,
    ResourceAddress,
    ResourceLifecycle,
)
from loommux.resource.policy import LeaseMode, LeasePolicy, LeasePolicyManager
from loommux.resource.routing import (
    OPERATOR_HEADER,
    RESOURCE_HEADER,
    ResourceRoutingError,
    resolve_address,
    resolve_client,
)

__all__ = [
    "ClientLease",
    "KernelResource",
    "KernelResourceManager",
    "LeaseClient",
    "LeaseMode",
    "LeasePolicy",
    "LeasePolicyManager",
    "ResourceAddress",
    "ResourceBusyError",
    "ResourceLifecycle",
    "ResourceManagerError",
    "ResourceNotFoundError",
    "ResourceProvisionError",
    "ResourceRoutingError",
    "RESOURCE_HEADER",
    "OPERATOR_HEADER",
    "resolve_address",
    "resolve_client",
]
