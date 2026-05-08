function [quitProgram] = initAllStim()
%% Constants
% Buttons
BUTTON_QUIT = 'Quit';
BUTTON_TRY = 'Start Over';  % start over button
TITLE_ERROR = 'ERROR';
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
errInitAllStim = 1;                       	% initialize errors
quitProgram = false;

%% Function
while errInitAllStim ~= 0                   % get stimulator(s) initialized
    fprintf('Initializing stimulator(s)...');	% initializing stimulator(s)
    errInitAllStim = PS_InitAllStim();      % get errors
    switch errInitAllStim                       % getting errors
        case 0                                  % no errors
           fprintf('OK.\n');   % stimulator(s) initialized
        case 1                                                          % initializing error
           prompt1 = 'ERROR INITIALIZING STIMULATOR(S)';	% stimulator(s) not initialized
           prompt2 = 'Turn the PlexStim off then on.';
           prompt = {prompt1,prompt2};
           fprintf('\n%s',prompt1);
           quest = questdlg(prompt,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
        case 2                                                          % no stimulator(s)
           msg = 'ERROR INITIALIZING STIMULATOR(S)';                    % stimulator(s) not initialized
           fprintf('\n%s',msg);
           quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
    end
    if errInitAllStim ~= 0
        switch quest                        % apply choice
            case BUTTON_QUIT               	% quit
                fprintf('\nQuitting...\n\n');	% quitting
                quitProgram = true;
                return;                     % exit program
            case BUTTON_TRY                     % try again
                fprintf('\nTrying again...\n\n'); % trying again
                PS_CloseAllStim();              % already initialized cause errors
                pause(5);                      % pause for 20 s for PlexStim to restart
            otherwise                       % cancel
                fprintf('\nQuitting...\n\n');	% quitting
                quitProgram = true;
                return;                     % exit program
        end
    end
end

end