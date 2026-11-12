package com.ziyueguyi.qingdan;

import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.work.Data;
import androidx.work.ListenableWorker.Result;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.assertEquals;

@RunWith(AndroidJUnit4.class)
public class WorkerOutcomeTest {
    @Test public void completedAndNotDueAreSuccessful() {
        assertEquals(Result.success(), WorkerOutcome.fromResponse("{\"ok\":true,\"data\":{\"ran\":false,\"reason\":\"not_due\"}}"));
        assertEquals(Result.success(), WorkerOutcome.fromResponse("{\"ok\":true,\"data\":{\"ran\":true,\"ok\":true}}"));
    }

    @Test public void businessFailureDoesNotBecomeSuccess() {
        assertEquals(Result.retry(), WorkerOutcome.fromResponse("{\"ok\":true,\"data\":{\"ran\":true,\"ok\":false,\"error_code\":\"SYNC_FAILED\"}}"));
        assertEquals(Result.retry(), WorkerOutcome.fromResponse("{\"ok\":false,\"error\":{\"code\":\"NETWORK\"}}"));
        assertEquals(Result.retry(), WorkerOutcome.fromResponse("not JSON"));
        assertEquals(Result.retry(), WorkerOutcome.fromResponse("{}"));
        assertEquals(Result.retry(), WorkerOutcome.fromResponse("{\"ok\":true}"));
    }

    @Test public void credentialsRequireUserActionInsteadOfAutomaticRetry() {
        Result expected = Result.failure(new Data.Builder().putString("error_code", "AUTH_REQUIRED").build());
        assertEquals(expected, WorkerOutcome.fromResponse("{\"ok\":false,\"error\":{\"code\":\"AUTH_REQUIRED\"}}"));
        assertEquals(expected, WorkerOutcome.fromResponse("{\"ok\":true,\"data\":{\"ran\":true,\"ok\":false,\"error_code\":\"AUTH_REQUIRED\"}}"));
    }
}
