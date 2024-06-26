import React, { useEffect, useContext, useCallback } from "react";

import Header from "./Components/Headers";
import Products from "./Components/ProductTypes/Products";
import Items from "./Components/ProductTypes/Items";
import Context from "./Context";

import styles from "./App.module.scss";

import { AUTH_TOKEN, API_URL } from "./constants";

const App = () => {
  const { isLoggedIn, linkSuccess, isItemAccess, isPaymentInitiation, dispatch } = useContext(Context);

  const me = useCallback(
    async () => {
      const token = window.localStorage.getItem(AUTH_TOKEN);
      try {
        if (token) {
          const response = await fetch(`${API_URL}/api/auth/me`, {
            method: "GET",
            headers: {
              authorization: token,
              'Accept': 'application/json',
              'Content-Type': 'application/json'
            }
          });
          if (response.ok) {
            const data = await response.json();
            if (data) {
              dispatch({ type: "SET_STATE", state: { userId: data.id } });
              dispatch({ type: "SET_STATE", state: { userFirstName: data.firstName } });
            }
            return;
          }
        } else {
          return;
        }
      } catch (err: any) {
        return "There was an issue with your request.";
      }
    },
    [dispatch]
  )

  const generateLinkToken = useCallback(
    async () => {
      const token = window.localStorage.getItem(AUTH_TOKEN);
      if (!token) {
        return;
      }
      // Link tokens for 'payment_initiation' use a different creation flow in your backend.
      const path = "/api/plaid/create_link_token";
      const response = await fetch(`${API_URL}${path}`, {
        method: "GET",
        headers: {
          authorization: token,
          'Accept': 'application/json',
          'Content-Type': 'application/json'
        }
      });
      if (!response.ok) {
        dispatch({ type: "SET_STATE", state: { linkToken: null } });
        return;
      }
      const data = await response.json();
      if (data) {
        if (data.error != null) {
          dispatch({
            type: "SET_STATE",
            state: {
              linkToken: null,
              linkTokenError: data.error,
            },
          });
          return;
        }
        dispatch({ type: "SET_STATE", state: { linkToken: data.link_token } });
      }
      // Save the link_token to be used later in the Oauth flow.
      localStorage.setItem("link_token", data.link_token);
    },
    [dispatch]
  );

  useEffect(() => {
    if (localStorage.getItem(AUTH_TOKEN)) {
      dispatch({ type: "SET_STATE", state: { isLoggedIn: true } });
    } else {
      dispatch({ type: "SET_STATE", state: { isLoggedIn: false } });
    }

    const init = async () => {
      // do not generate a new token for OAuth redirect; instead
      // setLinkToken from localStorage
      if (window.location.href.includes("?oauth_state_id=")) {
        dispatch({
          type: "SET_STATE",
          state: {
            linkToken: localStorage.getItem("link_token"),
          },
        });
        return;
      }
      generateLinkToken();
    };
    if (isLoggedIn) {
      init();
      me();
    }
  }, [isLoggedIn, dispatch, generateLinkToken, me]);

  return (
    <div className={styles.App}>
      <div className={styles.container}>
        <Header />
        {linkSuccess && (
          <>
            {isPaymentInitiation && (
              <Products />
            )}
            {isItemAccess && (
              <>
                <Products />
                <Items />
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
};

export default App;
