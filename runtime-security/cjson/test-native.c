#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <pthread.h>
#include "cJSON.h"
#include "cJSON_Utils.h"

static void patch_expect(const char *document, const char *patch, int sensitive,
                         int should_succeed, const char *expected)
{
    cJSON *root = cJSON_Parse(document);
    cJSON *operations = cJSON_Parse(patch);
    char *actual;
    int status;
    assert(root && operations);
    status = sensitive ? cJSONUtils_ApplyPatchesCaseSensitive(root, operations)
                       : cJSONUtils_ApplyPatches(root, operations);
    assert((status == 0) == should_succeed);
    actual = cJSON_PrintUnformatted(root);
    assert(actual != NULL);
    if (strcmp(actual, expected) != 0) {
        fprintf(stderr, "expected %s, got %s (status %d)\n", expected, actual, status);
        abort();
    }
    cJSON_free(actual);
    cJSON_Delete(operations);
    cJSON_Delete(root);
}

static void positive_controls(void)
{
    patch_expect("[{\"id\":0},{\"id\":1},{\"id\":2}]",
                 "[{\"op\":\"move\",\"from\":\"/0\",\"path\":\"/1/new\"}]",
                 0, 1, "[{\"id\":1},{\"id\":2,\"new\":{\"id\":0}}]");
    patch_expect("[{\"id\":0},{\"id\":1},{\"id\":2}]",
                 "[{\"op\":\"move\",\"from\":\"/0\",\"path\":\"/1/new\"}]",
                 1, 1, "[{\"id\":1},{\"id\":2,\"new\":{\"id\":0}}]");
    patch_expect("{\"b\":2,\"a\":1}",
                 "[{\"op\":\"test\",\"path\":\"\",\"value\":{\"a\":1,\"b\":2}},{\"op\":\"add\",\"path\":\"/c\",\"value\":3}]",
                 1, 1, "{\"b\":2,\"a\":1,\"c\":3}");
    patch_expect("{\"a/b\":1}", "[{\"op\":\"replace\",\"path\":\"/a~1b\",\"value\":2}]",
                 1, 1, "{\"a/b\":2}");
    patch_expect("{\"m~n\":1}", "[{\"op\":\"remove\",\"path\":\"/m~0n\"}]",
                 1, 1, "{}");
    patch_expect("{\"a~/b\":1}", "[{\"op\":\"copy\",\"from\":\"/a~0~1b\",\"path\":\"/q\"}]",
                 1, 1, "{\"a~/b\":1,\"q\":1}");
    patch_expect("{\"a\":1}", "[{\"op\":\"add\",\"path\":\"/b\",\"value\":2},{\"op\":\"test\",\"path\":\"/b\",\"value\":2}]",
                 1, 1, "{\"a\":1,\"b\":2}");
    patch_expect("{\"a\":1,\"b\":2}", "[{\"op\":\"move\",\"from\":\"/a\",\"path\":\"/c\"}]",
                 1, 1, "{\"b\":2,\"c\":1}");
    patch_expect("[1,2,3]", "[{\"op\":\"move\",\"from\":\"/0\",\"path\":\"/2\"}]",
                 1, 1, "[2,3,1]");
    patch_expect("{\"A\":1}", "[{\"op\":\"replace\",\"path\":\"/a\",\"value\":2}]",
                 0, 1, "{\"a\":2}");
    patch_expect("{\"A\":1}", "[{\"op\":\"replace\",\"path\":\"/A\",\"value\":2}]",
                 1, 1, "{\"A\":2}");
    patch_expect("{\"A\":1}", "[{\"op\":\"add\",\"path\":\"/a\",\"value\":2}]",
                 0, 1, "{\"a\":2}");
    patch_expect("{\"A\":1}", "[{\"op\":\"remove\",\"path\":\"/a\"}]",
                 0, 1, "{}");
    patch_expect("{\"A\":1}", "[{\"op\":\"copy\",\"from\":\"/a\",\"path\":\"/b\"}]",
                 0, 1, "{\"A\":1,\"b\":1}");
    patch_expect("{\"A\":1}", "[{\"op\":\"move\",\"from\":\"/a\",\"path\":\"/b\"}]",
                 0, 1, "{\"b\":1}");
    patch_expect("{\"A\":1}", "[{\"op\":\"test\",\"path\":\"/a\",\"value\":1}]",
                 0, 1, "{\"A\":1}");
    patch_expect("{\"a\":[1]}", "[{\"op\":\"copy\",\"from\":\"/a\",\"path\":\"/b\"}]",
                 1, 1, "{\"a\":[1],\"b\":[1]}");
}

static void rejection_controls(void)
{
    patch_expect("[{\"id\":0},{\"id\":1},{\"id\":2}]",
                 "[{\"op\":\"move\",\"from\":\"/0\",\"path\":\"/2/new\"}]",
                 0, 0, "[{\"id\":0},{\"id\":1},{\"id\":2}]");
    patch_expect("[{\"id\":0},{\"id\":1},{\"id\":2}]",
                 "[{\"op\":\"move\",\"from\":\"/0\",\"path\":\"/2/new\"}]",
                 1, 0, "[{\"id\":0},{\"id\":1},{\"id\":2}]");
    patch_expect("[1,2]", "[{\"op\":\"add\",\"path\":\"/0~1x\",\"value\":3}]",
                 0, 0, "[1,2]");
    patch_expect("[1,2]", "[{\"op\":\"add\",\"path\":\"/0~1x\",\"value\":3}]",
                 1, 0, "[1,2]");
    patch_expect("{\"a~2b\":1}", "[{\"op\":\"remove\",\"path\":\"/a~2b\"}]",
                 1, 0, "{\"a~2b\":1}");
    patch_expect("{\"a\":1}", "[{\"op\":\"replace\",\"path\":\"/a~\",\"value\":2}]",
                 1, 0, "{\"a\":1}");
    patch_expect("{\"a\":1}", "[{\"op\":\"move\",\"from\":\"/a\",\"path\":\"/bad~2\"}]",
                 1, 0, "{\"a\":1}");
    patch_expect("{\"a\":1}", "[{\"op\":\"move\",\"from\":\"/bad~2\",\"path\":\"/b\"}]",
                 1, 0, "{\"a\":1}");
    patch_expect("{\"A\":1}", "[{\"op\":\"remove\",\"path\":\"/a\"}]",
                 1, 0, "{\"A\":1}");
    patch_expect("{\"a\":[1]}", "[{\"op\":\"copy\",\"from\":\"/a\",\"path\":\"/a/0/x\"}]",
                 1, 0, "{\"a\":[1]}");
    patch_expect("[1,2,3]", "[{\"op\":\"move\",\"from\":\"/0\",\"path\":\"/3\"}]",
                 1, 0, "[1,2,3]");
}

/* JSON Patch treats a reference as a value. Mutation through its parent must
 * not detach or free a child that is still owned by another wrapper. */
static void borrowed_parent_controls(void)
{
    const char *patches[] = {
        "[{\"op\":\"move\",\"from\":\"/ref/x\",\"path\":\"/q\"}]",
        "[{\"op\":\"add\",\"path\":\"/ref/y\",\"value\":2}]",
        "[{\"op\":\"replace\",\"path\":\"/ref/x\",\"value\":2}]",
        "[{\"op\":\"remove\",\"path\":\"/ref/x\"}]",
        "[{\"op\":\"copy\",\"from\":\"/owner/x\",\"path\":\"/ref/y\"}]",
        "[{\"op\":\"move\",\"from\":\"/owner/x\",\"path\":\"/ref/y\"}]"
    };
    const char *expected[] = {
        "{\"owner\":{\"x\":1},\"ref\":{},\"q\":1}",
        "{\"owner\":{\"x\":1},\"ref\":{\"x\":1,\"y\":2}}",
        "{\"owner\":{\"x\":1},\"ref\":{\"x\":2}}",
        "{\"owner\":{\"x\":1},\"ref\":{}}",
        "{\"owner\":{\"x\":1},\"ref\":{\"x\":1,\"y\":1}}",
        "{\"owner\":{},\"ref\":{\"x\":1,\"y\":1}}"
    };
    unsigned i;
    int sensitive;
    for (sensitive = 0; sensitive < 2; sensitive++) {
        for (i = 0; i < sizeof(patches) / sizeof(patches[0]); i++) {
            cJSON *document = cJSON_Parse("{\"owner\":{\"x\":1}}");
            cJSON *owner = cJSON_GetObjectItemCaseSensitive(document, "owner");
            cJSON *reference = cJSON_CreateObjectReference(owner->child);
            cJSON *operations = cJSON_Parse(patches[i]);
            char *actual;
            assert(document && owner && reference && operations);
            assert(cJSON_AddItemToObject(document, "ref", reference));
            assert((sensitive ? cJSONUtils_ApplyPatchesCaseSensitive(document, operations) :
                                cJSONUtils_ApplyPatches(document, operations)) == 0);
            actual = cJSON_PrintUnformatted(document);
            assert(actual && strcmp(actual, expected[i]) == 0);
            cJSON_free(actual);
            cJSON_Delete(operations);
            cJSON_Delete(document);
        }
    }
    {
        cJSON *external = cJSON_Parse("{\"x\":1}");
        cJSON *document = cJSON_CreateObject();
        cJSON *reference = cJSON_CreateObjectReference(external->child);
        cJSON *operations = cJSON_Parse(patches[0]);
        char *actual;
        assert(external && document && reference && operations);
        assert(cJSON_AddItemToObject(document, "ref", reference));
        assert(cJSONUtils_ApplyPatchesCaseSensitive(document, operations) == 0);
        actual = cJSON_PrintUnformatted(external);
        assert(actual && strcmp(actual, "{\"x\":1}") == 0);
        cJSON_free(actual);
        cJSON_Delete(operations);
        cJSON_Delete(document);
        cJSON_Delete(external);
    }
    {
        cJSON *external = cJSON_Parse("{\"x\":1}");
        cJSON *parent = cJSON_Parse("{\"left\":0}");
        cJSON *document = cJSON_CreateObjectReference(external->child);
        cJSON *operations = cJSON_Parse("[{\"op\":\"replace\",\"path\":\"/x\",\"value\":3},{\"op\":\"replace\",\"path\":\"\",\"value\":{\"final\":4}}]");
        const char *name = "slot";
        cJSON *previous;
        cJSON *next;
        char *actual;
        assert(external && parent && document && operations);
        assert(cJSON_AddItemToObjectCS(parent, name, document));
        assert(cJSON_AddNumberToObject(parent, "right", 2));
        previous = document->prev;
        next = document->next;
        assert(cJSONUtils_ApplyPatchesCaseSensitive(document, operations) == 0);
        assert(document->prev == previous && document->next == next);
        assert(document->string == name && (document->type & cJSON_StringIsConst));
        assert(!(document->type & cJSON_IsReference));
        actual = cJSON_PrintUnformatted(parent);
        assert(actual && strcmp(actual, "{\"left\":0,\"slot\":{\"final\":4},\"right\":2}") == 0);
        cJSON_free(actual);
        assert(cJSON_GetObjectItemCaseSensitive(external, "x")->valueint == 1);
        cJSON_Delete(operations);
        cJSON_Delete(parent);
        cJSON_Delete(external);
    }
    {
        cJSON *external = cJSON_Parse("{\"x\":1}");
        cJSON *document = cJSON_CreateObjectReference(external->child);
        cJSON *test = cJSON_Parse("[{\"op\":\"test\",\"path\":\"/x\",\"value\":1}]");
        cJSON *sequence = cJSON_Parse("[{\"op\":\"replace\",\"path\":\"/x\",\"value\":2},{\"op\":\"remove\",\"path\":\"/missing\"}]");
        assert(external && document && test && sequence);
        assert(cJSONUtils_ApplyPatchesCaseSensitive(document, test) == 0);
        assert(document->child == external->child && (document->type & cJSON_IsReference));
        assert(cJSONUtils_ApplyPatchesCaseSensitive(document, sequence) != 0);
        assert(cJSON_GetObjectItemCaseSensitive(document, "x")->valueint == 2);
        assert(cJSON_GetObjectItemCaseSensitive(external, "x")->valueint == 1);
        cJSON_Delete(sequence);
        cJSON_Delete(test);
        cJSON_Delete(document);
        cJSON_Delete(external);
    }
}

static cJSON *deep_array(unsigned count)
{
    cJSON *root = cJSON_CreateArray();
    cJSON *cursor = root;
    unsigned i;
    assert(root);
    for (i = 1; i < count; i++) {
        cJSON *next = cJSON_CreateArray();
        assert(next && cJSON_AddItemToArray(cursor, next));
        cursor = next;
    }
    assert(cJSON_AddItemToArray(cursor, cJSON_CreateNumber(1)));
    return root;
}

static cJSON *deep_object(unsigned count)
{
    cJSON *root = cJSON_CreateObject();
    cJSON *cursor = root;
    unsigned i;
    assert(root);
    for (i = 1; i < count; i++) {
        cJSON *next = cJSON_CreateObject();
        assert(next && cJSON_AddItemToObject(cursor, "x", next));
        cursor = next;
    }
    assert(cJSON_AddNumberToObject(cursor, "leaf", 1));
    return root;
}

static void depth_and_comparison(void)
{
    cJSON *a = deep_array(999);
    cJSON *b = deep_array(999);
    cJSON *object = cJSON_CreateObject();
    cJSON *patch = cJSON_Parse("[{\"op\":\"copy\",\"from\":\"/deep\",\"path\":\"/holder/too_deep\"}]");
    cJSON *holder = cJSON_CreateObject();
    cJSON *nested_a = deep_object(400);
    cJSON *nested_b = deep_object(400);
    char *before;
    char *after;
    clock_t start;
    assert(a && b && object && patch && holder && nested_a && nested_b);
    assert(cJSON_Compare(a, b, 1));
    start = clock();
    assert(cJSON_Compare(nested_a, nested_b, 1));
    assert((double)(clock() - start) / CLOCKS_PER_SEC < 2.0);
    assert(cJSON_Compare(nested_a, nested_b, 0));
    assert(cJSON_AddItemToObject(object, "deep", a));
    assert(cJSON_AddItemToObject(object, "holder", holder));
    before = cJSON_PrintUnformatted(object);
    assert(before);
    assert(cJSONUtils_ApplyPatchesCaseSensitive(object, patch) != 0);
    after = cJSON_PrintUnformatted(object);
    assert(after && strcmp(before, after) == 0);
    cJSON_free(before);
    cJSON_free(after);
    cJSON_Delete(patch);
    cJSON_Delete(object);
    cJSON_Delete(b);
    cJSON_Delete(nested_a);
    cJSON_Delete(nested_b);
    patch_expect("{\"a\":[1],\"holder\":{}}",
                 "[{\"op\":\"copy\",\"from\":\"/a\",\"path\":\"/holder/b\"}]",
                 1, 1, "{\"a\":[1],\"holder\":{\"b\":[1]}}");
}

static void duplicate_and_reference_controls(void)
{
    cJSON *a = cJSON_Parse("{\"k\":1,\"k\":2}");
    cJSON *b = cJSON_Parse("{\"k\":1,\"k\":2}");
    cJSON *c = cJSON_Parse("{\"k\":1,\"k\":3}");
    cJSON *reordered = cJSON_Parse("{\"b\":2,\"a\":1}");
    cJSON *ordered = cJSON_Parse("{\"a\":1,\"b\":2}");
    cJSON *case_a = cJSON_Parse("{\"A\":1}");
    cJSON *case_b = cJSON_Parse("{\"a\":1}");
    cJSON *child = cJSON_Parse("{\"x\":1}");
    cJSON *reference = cJSON_CreateObjectReference(child->child);
    assert(a && b && c && reordered && ordered && case_a && case_b && child && reference);
    assert(!cJSON_Compare(a, b, 1)); /* Original first-key duplicate semantics. */
    assert(!cJSON_Compare(a, c, 1));
    assert(cJSON_Compare(reordered, ordered, 1));
    assert(cJSON_Compare(case_a, case_b, 0));
    assert(!cJSON_Compare(case_a, case_b, 1));
    assert(cJSON_Compare(child, reference, 1));
    {
        cJSON *reference_copy = cJSON_Duplicate(reference, 1);
        assert(reference_copy && cJSON_Compare(child, reference_copy, 1));
        cJSON_Delete(reference_copy);
    }
    cJSON_Delete(reference);
    cJSON_Delete(child);
    cJSON_Delete(a);
    cJSON_Delete(b);
    cJSON_Delete(c);
    cJSON_Delete(reordered);
    cJSON_Delete(ordered);
    cJSON_Delete(case_a);
    cJSON_Delete(case_b);
    {
        cJSON *root = cJSON_Parse("{\"owner\":{\"x\":1}}");
        cJSON *operations = cJSON_Parse("[{\"op\":\"move\",\"from\":\"/ref\",\"path\":\"\"}]");
        cJSON *owner = cJSON_GetObjectItemCaseSensitive(root, "owner");
        cJSON *borrowed = cJSON_CreateObjectReference(owner->child);
        char *actual;
        assert(root && operations && owner && borrowed && cJSON_AddItemToObject(root, "ref", borrowed));
        assert(cJSONUtils_ApplyPatchesCaseSensitive(root, operations) == 0);
        actual = cJSON_PrintUnformatted(root);
        assert(actual && strcmp(actual, "{\"x\":1}") == 0);
        cJSON_free(actual);
        cJSON_Delete(operations);
        cJSON_Delete(root);
    }
    {
        cJSON *root = cJSON_Parse("{\"owner\":{\"x\":1}}");
        cJSON *operations = cJSON_Parse("[{\"op\":\"move\",\"from\":\"/ref\",\"path\":\"/owner\"}]");
        cJSON *owner = cJSON_GetObjectItemCaseSensitive(root, "owner");
        cJSON *borrowed = cJSON_CreateObjectReference(owner->child);
        char *actual;
        assert(root && operations && owner && borrowed && cJSON_AddItemToObject(root, "ref", borrowed));
        assert(cJSONUtils_ApplyPatchesCaseSensitive(root, operations) == 0);
        actual = cJSON_PrintUnformatted(root);
        assert(actual && strcmp(actual, "{\"owner\":{\"x\":1}}") == 0);
        cJSON_free(actual);
        cJSON_Delete(operations);
        cJSON_Delete(root);
    }
    {
        cJSON *outside = cJSON_Parse("{\"x\":1}");
        cJSON *root = cJSON_CreateObject();
        cJSON *operations = cJSON_Parse("[{\"op\":\"move\",\"from\":\"/ref\",\"path\":\"/dest\"}]");
        cJSON *borrowed = cJSON_CreateObjectReference(outside->child);
        char *actual;
        assert(outside && root && operations && borrowed && cJSON_AddItemToObject(root, "ref", borrowed));
        assert(cJSONUtils_ApplyPatchesCaseSensitive(root, operations) == 0);
        actual = cJSON_PrintUnformatted(root);
        assert(actual && strcmp(actual, "{\"dest\":{\"x\":1}}") == 0);
        cJSON_free(actual);
        assert(cJSON_GetObjectItemCaseSensitive(outside, "x") != NULL);
        cJSON_Delete(operations);
        cJSON_Delete(root);
        cJSON_Delete(outside);
    }
    {
        cJSON *root = cJSON_Parse("{\"owner\":{\"x\":1},\"src\":{}}");
        cJSON *operations = cJSON_Parse("[{\"op\":\"move\",\"from\":\"/src\",\"path\":\"/owner\"}]");
        cJSON *owner = cJSON_GetObjectItemCaseSensitive(root, "owner");
        cJSON *src = cJSON_GetObjectItemCaseSensitive(root, "src");
        cJSON *borrowed = cJSON_CreateObjectReference(owner->child);
        char *actual;
        assert(root && operations && owner && src && borrowed && cJSON_AddItemToObject(src, "nested", borrowed));
        assert(cJSONUtils_ApplyPatchesCaseSensitive(root, operations) == 0);
        actual = cJSON_PrintUnformatted(root);
        assert(actual && strcmp(actual, "{\"owner\":{\"nested\":{\"x\":1}}}") == 0);
        cJSON_free(actual);
        cJSON_Delete(operations);
        cJSON_Delete(root);
    }
}

static void cyclic_reference_control(void)
{
    cJSON *root = cJSON_CreateObject();
    cJSON *source = cJSON_CreateObject();
    cJSON *reference = cJSON_CreateObjectReference(source);
    cJSON *operations = cJSON_Parse("[{\"op\":\"move\",\"from\":\"/src\",\"path\":\"/other\"}]");
    assert(root && source && reference && operations);
    reference->child = source; /* Synthetic shared API graph with an active cycle. */
    assert(cJSON_AddItemToObject(source, "loop", reference));
    assert(cJSON_AddItemToObject(root, "src", source));
    assert(cJSONUtils_ApplyPatchesCaseSensitive(root, operations) != 0);
    assert(cJSON_GetObjectItemCaseSensitive(root, "src") == source);
    assert(cJSON_GetObjectItemCaseSensitive(root, "other") == NULL);
    cJSON_Delete(operations);
    cJSON_Delete(root);
}

static void *bounded_stack_control(void *unused)
{
    cJSON *root = cJSON_CreateObject();
    cJSON *deep = deep_array(999);
    cJSON *holder = cJSON_CreateObject();
    cJSON *patch = cJSON_Parse("[{\"op\":\"copy\",\"from\":\"/deep\",\"path\":\"/holder/too_deep\"}]");
    (void)unused;
    assert(root && deep && holder && patch);
    assert(cJSON_AddItemToObject(root, "deep", deep));
    assert(cJSON_AddItemToObject(root, "holder", holder));
    assert(cJSONUtils_ApplyPatchesCaseSensitive(root, patch) != 0);
    assert(cJSON_GetObjectItemCaseSensitive(holder, "too_deep") == NULL);
    cJSON_Delete(patch);
    cJSON_Delete(root);
    return NULL;
}

static void stack_control(void)
{
    pthread_attr_t attributes;
    pthread_t thread;
    assert(pthread_attr_init(&attributes) == 0);
    assert(pthread_attr_setstacksize(&attributes, 512 * 1024) == 0);
    assert(pthread_create(&thread, &attributes, bounded_stack_control, NULL) == 0);
    assert(pthread_join(thread, NULL) == 0);
    assert(pthread_attr_destroy(&attributes) == 0);
}

static void *fail_alloc(size_t size) { (void)size; return NULL; }
static size_t fail_after;
static size_t allocation_count;
static void *nth_alloc(size_t size)
{
    if (allocation_count++ == fail_after) { return NULL; }
    return malloc(size);
}
static void borrowed_parent_allocation_failure(void)
{
    const char *patches[] = {
        "[{\"op\":\"move\",\"from\":\"/ref/x\",\"path\":\"/q\"}]",
        "[{\"op\":\"add\",\"path\":\"/ref/y\",\"value\":2}]",
        "[{\"op\":\"replace\",\"path\":\"/ref/x\",\"value\":2}]",
        "[{\"op\":\"remove\",\"path\":\"/ref/x\"}]",
        "[{\"op\":\"copy\",\"from\":\"/owner/x\",\"path\":\"/ref/y\"}]",
        "[{\"op\":\"move\",\"from\":\"/owner/x\",\"path\":\"/ref/y\"}]"
    };
    const char *expected[] = {
        "{\"owner\":{\"x\":1},\"ref\":{},\"q\":1}",
        "{\"owner\":{\"x\":1},\"ref\":{\"x\":1,\"y\":2}}",
        "{\"owner\":{\"x\":1},\"ref\":{\"x\":2}}",
        "{\"owner\":{\"x\":1},\"ref\":{}}",
        "{\"owner\":{\"x\":1},\"ref\":{\"x\":1,\"y\":1}}",
        "{\"owner\":{},\"ref\":{\"x\":1,\"y\":1}}"
    };
    size_t successes = 0;
    size_t refusals = 0;
    unsigned i;
    int sensitive;
    for (sensitive = 0; sensitive < 2; sensitive++) {
        for (i = 0; i < sizeof(patches) / sizeof(patches[0]); i++) {
            for (fail_after = 0; fail_after < 64; fail_after++) {
                cJSON *document = cJSON_Parse("{\"owner\":{\"x\":1}}");
                cJSON *owner = cJSON_GetObjectItemCaseSensitive(document, "owner");
                cJSON *reference = cJSON_CreateObjectReference(owner->child);
                cJSON *operations = cJSON_Parse(patches[i]);
                cJSON_Hooks hooks = { nth_alloc, free };
                char *actual;
                int status;
                assert(document && owner && reference && operations);
                assert(cJSON_AddItemToObject(document, "ref", reference));
                allocation_count = 0;
                cJSON_InitHooks(&hooks);
                status = sensitive ? cJSONUtils_ApplyPatchesCaseSensitive(document, operations) :
                                     cJSONUtils_ApplyPatches(document, operations);
                cJSON_InitHooks(NULL);
                actual = cJSON_PrintUnformatted(document);
                assert(actual);
                if (status == 0) {
                    assert(strcmp(actual, expected[i]) == 0);
                    successes++;
                } else {
                    assert(strcmp(actual, "{\"owner\":{\"x\":1},\"ref\":{\"x\":1}}") == 0);
                    assert(cJSON_GetObjectItemCaseSensitive(document, "owner") == owner);
                    assert(cJSON_GetObjectItemCaseSensitive(document, "ref") == reference);
                    assert(reference->child == owner->child && (reference->type & cJSON_IsReference));
                    refusals++;
                }
                cJSON_free(actual);
                cJSON_Delete(operations);
                cJSON_Delete(document);
            }
        }
    }
    assert(successes > 0 && refusals > 0 && successes + refusals == 768);
    printf("borrowed_parent_allocation_controls=pass successes=%lu refusals=%lu\n",
           (unsigned long)successes, (unsigned long)refusals);
}

static void allocation_failure(void)
{
    cJSON *a = cJSON_Parse("{\"x\":1}");
    cJSON *b = cJSON_Parse("{\"x\":1}");
    cJSON_Hooks hooks = { fail_alloc, free };
    assert(a && b);
    cJSON_InitHooks(&hooks);
    assert(!cJSON_Compare(a, b, 1));
    cJSON_InitHooks(NULL);
    cJSON_Delete(a);
    cJSON_Delete(b);
    for (fail_after = 0; fail_after < 32; fail_after++) {
        cJSON *document = cJSON_Parse("{\"src\":{\"x\":[1,2]},\"dst\":{}}");
        cJSON *operations = cJSON_Parse("[{\"op\":\"copy\",\"from\":\"/src\",\"path\":\"/dst/copy\"}]");
        cJSON_Hooks nth_hooks = { nth_alloc, free };
        char *before;
        char *after;
        int status;
        assert(document && operations);
        before = cJSON_PrintUnformatted(document);
        assert(before);
        allocation_count = 0;
        cJSON_InitHooks(&nth_hooks);
        status = cJSONUtils_ApplyPatchesCaseSensitive(document, operations);
        cJSON_InitHooks(NULL);
        after = cJSON_PrintUnformatted(document);
        assert(after);
        if (status != 0) { assert(strcmp(before, after) == 0); }
        cJSON_free(before);
        cJSON_free(after);
        cJSON_Delete(document);
        cJSON_Delete(operations);
    }
    for (fail_after = 0; fail_after < 48; fail_after++) {
        cJSON *document = cJSON_Parse("{\"src\":{\"x\":[1,2]},\"dst\":{}}");
        cJSON *operations = cJSON_Parse("[{\"op\":\"move\",\"from\":\"/src\",\"path\":\"/dst/moved\"}]");
        cJSON_Hooks nth_hooks = { nth_alloc, free };
        char *before;
        char *after;
        int status;
        assert(document && operations);
        before = cJSON_PrintUnformatted(document);
        assert(before);
        allocation_count = 0;
        cJSON_InitHooks(&nth_hooks);
        status = cJSONUtils_ApplyPatchesCaseSensitive(document, operations);
        cJSON_InitHooks(NULL);
        after = cJSON_PrintUnformatted(document);
        assert(after);
        if (status != 0) { assert(strcmp(before, after) == 0); }
        else { assert(strcmp(after, "{\"dst\":{\"moved\":{\"x\":[1,2]}}}") == 0); }
        cJSON_free(before);
        cJSON_free(after);
        cJSON_Delete(document);
        cJSON_Delete(operations);
    }
    for (fail_after = 0; fail_after < 48; fail_after++) {
        cJSON *document = cJSON_Parse("{\"owner\":{\"x\":1}}");
        cJSON *operations = cJSON_Parse("[{\"op\":\"move\",\"from\":\"/ref\",\"path\":\"\"}]");
        cJSON *owner = cJSON_GetObjectItemCaseSensitive(document, "owner");
        cJSON *reference = cJSON_CreateObjectReference(owner->child);
        cJSON_Hooks nth_hooks = { nth_alloc, free };
        char *before;
        char *after;
        int status;
        assert(document && operations && owner && reference && cJSON_AddItemToObject(document, "ref", reference));
        before = cJSON_PrintUnformatted(document);
        assert(before);
        allocation_count = 0;
        cJSON_InitHooks(&nth_hooks);
        status = cJSONUtils_ApplyPatchesCaseSensitive(document, operations);
        cJSON_InitHooks(NULL);
        after = cJSON_PrintUnformatted(document);
        assert(after);
        if (status != 0) { assert(strcmp(before, after) == 0); }
        else { assert(strcmp(after, "{\"x\":1}") == 0); }
        cJSON_free(before);
        cJSON_free(after);
        cJSON_Delete(document);
        cJSON_Delete(operations);
    }
    {
        cJSON *deep = deep_array(130);
        for (fail_after = 0; fail_after < 145; fail_after++) {
            cJSON_Hooks nth_hooks = { nth_alloc, free };
            cJSON *copy;
            allocation_count = 0;
            cJSON_InitHooks(&nth_hooks);
            copy = cJSON_Duplicate(deep, 1);
            cJSON_InitHooks(NULL);
            if (copy != NULL) {
                assert(cJSON_Compare(deep, copy, 1));
                cJSON_Delete(copy);
            }
        }
        cJSON_Delete(deep);
    }
}

int main(void)
{
    positive_controls();
    rejection_controls();
    borrowed_parent_controls();
    depth_and_comparison();
    duplicate_and_reference_controls();
    cyclic_reference_control();
    stack_control();
    allocation_failure();
    borrowed_parent_allocation_failure();
    puts("cJSON native controls passed");
    return 0;
}
