# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

from abc import ABCMeta, abstractmethod
from collections.abc import Iterator
from urllib.parse import urljoin
from typing import Callable, Dict, Generic, List, Optional, Union, Type, TypeVar, KeysView, ItemsView
import smo.utils as utils


class NodeObject(metaclass=ABCMeta):
    @classmethod
    def get_nodes_for_parent(
            cls,
            root_server: 'Server',
            parent_obj: Optional['NodeObject'],
            context_args=None
    ) -> List['NodeObject']:
        """
        Renders and executes nodes.sql for the class to generate a list of NodeObjects
        :param root_server: Root node of the object model
        :param parent_obj: The object that is the parent of all objects generated by this method
        :return: A list of NodeObjects generated with _from_node_query
        """
        template_root = cls._template_root(root_server)

        # Only include a parent ID if a parent was provided
        template_vars = {}      # TODO: Allow configuring show/hide system objects
        if parent_obj is not None:
            template_vars['parent_id'] = parent_obj._oid
        elif context_args:
            template_vars = context_args

        # Render and execute the template
        sql = utils.templating.render_template(
            utils.templating.get_template_path(template_root, 'nodes.sql', root_server.version),
            macro_roots=cls._macro_root(),
            **template_vars
        )
        if parent_obj is None:
            cols, rows = root_server.connection.execute_dict(sql)
        else:
            database_node = parent_obj.get_database_node()
            cols, rows = database_node.connection.execute_dict(sql)

        return [cls._from_node_query(root_server, parent_obj, **row) for row in rows]

    @classmethod
    @abstractmethod
    def _from_node_query(cls, root_server: 'Server', parent: 'NodeObject', **kwargs) -> 'NodeObject':
        pass

    def __init__(self, root_server: 'Server', parent: Optional['NodeObject'], name: str):
        # Define the state of the object
        self._server: 'Server' = root_server
        self._parent: Optional['NodeObject'] = parent

        self._child_collections: Dict[str, NodeCollection] = {}
        self._property_collections: List[NodeLazyPropertyCollection] = []
        self._full_properties: NodeLazyPropertyCollection = self._register_property_collection(self._property_generator)

        # Declare node basic properties
        self._name: str = name
        self._oid: Optional[int] = None
        self._is_system: bool = False

    # PROPERTIES ###########################################################
    @property
    def name(self) -> str:
        return self._name

    @property
    def is_system(self) -> bool:
        return self._is_system

    @property
    def oid(self) -> Optional[int]:
        return self._oid

    @property
    def parent(self) -> Optional['NodeObject']:
        return self._parent

    @property
    def urn(self) -> str:
        """
        The URN for this instance of the node object. Generated by recursively traversing up the
        tree until the object doesn't have a parent. The root of the URN is provided by the Server.
        """
        collection = self.__class__.__name__
        this_fragment = f'{collection}.{self.oid}/'
        if self.parent is None:
            # Base case: object does not have a parent. Append the fragment to the server URN
            return urljoin(self.server.urn_base, this_fragment)
        else:
            # Recursive case: object has a parent. Append the fragment to the parent's URN
            return urljoin(self.parent.urn, this_fragment)

    @property
    def server(self) -> 'Server':
        return self._server

    @property
    def extended_vars(self) -> dict:
        return {}

    @property
    def template_vars(self) -> dict:
        template_vars = {"oid": self.oid}
        extended_vars = self.extended_vars
        return {**template_vars, **extended_vars}

    # METHODS ##############################################################
    def get_object_by_urn(self, urn_fragment: str) -> 'NodeObject':
        """
        Finds an object using a relative URN
        :param urn_fragment: Fragment of a URN, relative to this object class
        :return: The NodeObject that matches up with the URN
        """
        # Base case: The fragment points to this instance
        if urn_fragment == '/':
            return self

        # Recursive case: Process the fragment and recurse
        class_name, oid, remaining = utils.process_urn(urn_fragment)

        # Get the matching child collection
        collection = self._child_collections.get(class_name)
        if collection is None:
            raise ValueError(f'The URN fragment {class_name} is not supported by {self.__class__.__name__}')

        # Get the matching object
        # TODO: Create a .get method for NodeCollection (see https://github.com/Microsoft/carbon/issues/1713)
        obj = collection[oid]
        return obj.get_object_by_urn(remaining)

    def refresh(self) -> None:
        """Refreshes and lazily loaded data"""
        self._refresh_child_collections()

    def get_database_node(self) -> 'NodeObject':
        if self.parent is None:
            # checking for class name here. isInstance needs importing of Database class here creates circular dependency
            if self.__class__.__name__ == 'Database':
                return self
            else:
                return None
        else:
            return self.parent.get_database_node()

    # STATIC HELPERS #######################################################
    @classmethod
    def _macro_root(cls) -> Optional[List[str]]:
        """Optionally add additional paths to macros for template rendering"""
        return None

    @classmethod
    @abstractmethod
    def _template_root(cls, root_server: 'Server') -> str:
        """
        Required to be implemented in child classes. Returns the path to the root of templates for the object
        :param root_server: The server that the object belongs to
        :return: Path to the root of templates used by this class
        """

    # PROTECTED HELPERS ####################################################
    TRCC = TypeVar('TRCC')

    def _register_child_collection(self, class_: Type[TRCC]) -> 'NodeCollection[TRCC]':
        """
        Creates a node collection for child objects and registers it with the list of child objects.
        This is very useful for ensuring that all child collections are reset when refreshing.
        :param generator: Callable for generating the list of nodes
        :return: The created node collection
        """
        collection = NodeCollection(lambda: class_.get_nodes_for_parent(self.server, self))
        self._child_collections[class_.__name__] = collection
        return collection

    def _register_property_collection(self, generator: Callable[[], Dict[str, Optional[Union[str, int, bool]]]]):
        """
        Creates a property collection for extended properties, etc, and registers with the list of
        property collections.
        :param generator: The generator for the property collection
        :return: The created property collection
        """
        collection = NodeLazyPropertyCollection(generator)
        self._property_collections.append(collection)
        return collection

    # PRIVATE HELPERS ######################################################
    def _property_generator(self) -> Dict[str, Optional[Union[str, int, bool]]]:
        template_root = self._template_root(self._server)

        # Setup the parameters for the query
        template_vars = self.template_vars

        # Render and execute the template
        sql = utils.templating.render_template(
            utils.templating.get_template_path(template_root, 'properties.sql', self._server.version),
            self._macro_root(),
            **template_vars
        )
        cols, rows = self._server.connection.execute_dict(sql)

        if len(rows) > 0:
            return rows[0]

    def _additional_property_generator(self) -> Dict[str, Optional[Union[str, int, bool]]]:
        """Gets any additional properties if defined in a sql file"""
        template_root = self._template_root(self._server)

        # Setup the parameters for the query
        template_vars = self.template_vars

        # Render and execute the template
        sql = utils.templating.render_template(
            utils.templating.get_template_path(template_root, 'additional_properties.sql', self._server.version),
            **template_vars
        )
        cols, rows = self._server.connection.execute_dict(sql)

        if len(rows) > 0:
            return rows[0]

    def _refresh_child_collections(self) -> None:
        """Iterates over the registered child collections and property collections and resets them"""
        for key, node_collection in self._child_collections.items():
            node_collection.reset()

        for prop_collection in self._property_collections:
            prop_collection.reset()


class NodeLazyPropertyCollection:
    def __init__(self, generator: Callable[[], Dict[str, Optional[Union[str, int, bool]]]]):
        """
        Initializes a new lazy property collection with a generator to call when looking up the properties
        :param generator: A callable that returns a dictionary of properties when called
        """
        self._generator: Callable[[], Dict[str, Optional[Union[str, int, bool]]]] = generator
        self._items_impl: Optional[Dict[str, Optional[Union[str, int, bool]]]] = None

    @property
    def _items(self) -> Dict[str, Optional[Union[str, int, bool]]]:
        """Property that ensures properties are loaded before returning the properties"""
        if self._items_impl is None:
            self._items_impl = self._generator()
        return self._items_impl

    def __getitem__(self, index: str) -> any:
        """
        Searches for a property and returns it. If the collection of properties hasn't been loaded,
        load it.
        :param item: The index of the item to get from the property collection
        :raises TypeError: If index is not a string
        :raises NameError: If an item with the provided index does not exist
        :return: The value of the item in the property collection
        """
        # Make sure we have a valid index
        if not isinstance(index, str):
            raise TypeError('Index must be a string')

        return self._items[index]

    def __iter__(self) -> Iterator:
        return self._items.__iter__()

    def __len__(self) -> int:
        return len(self._items)

    def get(self, item: str, default: Optional[Union[str, int, bool]] = None) -> Optional[Union[str, int, bool]]:
        return self._items.get(item, default)

    def items(self) -> ItemsView[str, Union[str, int, bool]]:
        return self._items.items()

    def keys(self) -> KeysView[str]:
        return self._items.keys()

    def reset(self) -> None:
        # Empty the items so that the next request will reload the collection
        self._items_impl = None


TNC = TypeVar('TNC')


class NodeCollection(Generic[TNC]):
    def __init__(self, generator: Callable[[], List[TNC]]):
        """
        Initializes a new collection of node objects.
        :param generator: A callable that returns a list of NodeObjects when called
        """
        self._generator: Callable[[], List[TNC]] = generator
        self._items_impl: Optional[List[TNC]] = None

    @property
    def _items(self) -> List[TNC]:
        # Load the items if they haven't been loaded
        if self._items_impl is None:
            self._items_impl = self._generator()

        # noinspection PyTypeChecker
        # - This should always be a list b/c _ensure_loaded will load the list if it is None
        return self._items_impl

    def __getitem__(self, index: Union[int, str]) -> TNC:
        """
        Searches for a node in the list of items by OID or name
        :param index: If an int, the object ID of the item to look up. If a str, the name of the
                      item to look up. Otherwise, TypeError will be raised.
        :raises TypeError: If index is not a str or int
        :raises NameError: If an item with the provided index does not exist
        :return: The instance that matches the provided index
        """
        # Determine how we will be looking up the item
        if isinstance(index, int):
            # Lookup is by object ID
            lookup = (lambda x: x.oid == index)
        elif isinstance(index, str):
            # Lookup is by object name
            lookup = (lambda x: x.name == index)
        else:
            raise TypeError('Index must be either a string or int')

        # Look up the desired item
        for item in self._items:
            if lookup(item):
                return item

        # If we make it to here, an item with the given index does not exist
        raise NameError('An item with the provided index does not exist')       # TODO: Localize?

    def __iter__(self) -> Iterator:
        return self._items.__iter__()

    def __len__(self) -> int:
        # Load the items if they haven't been loaded
        return len(self._items)

    def reset(self) -> None:
        # Empty the items so that next iteration will reload the collection
        self._items_impl = None
    
    def refresh(self) -> None:
        """Refreshes and lazily loaded data"""
        self.reset()
