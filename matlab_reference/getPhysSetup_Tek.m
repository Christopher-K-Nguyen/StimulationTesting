function [File,quitProgram] = getPhysSetup_Tek(File)
%% Constants
% Buttons
BUTTON_YES = 'Yes';
BUTTON_NO = 'No';
BUTTON_OK = 'OK';
BUTTON_QUIT = 'Quit';
% Titles
% Options
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
confirmPhysSetup = false;       % initialize setup
quitProgram = false;

%% Function
% % Define the name of the process to close
% processName = 'Stim-2.exe'; % This is the process name for Microsoft Word
% 
% % Check if the process is running
% command = ['tasklist /FI "IMAGENAME eq ' processName ' /FI "STATUS eq running"'];
% [status,~] = system(command);
% isRunning = status == 0;
% 
% % If the process is running, then close it
% if isRunning
%     % Use the system command to close the process
%     system(['taskkill /F /IM ' processName]);
% end

while ~confirmPhysSetup         	% check setup
    % Notice
    prompt1 = 'This code ONLY works for:';
    prompt2 = '\bullet 1 Plexon PlexStim Stimulator System';
    prompt3 = '\bullet Up to 2 Tektronix oscilloscope via USB or RS232';
    prompt = {prompt1,prompt2,prompt3};
    opts.Default = BUTTON_OK;       % option dedault
    notice = questdlg( ...
        prompt, ...
        'NOTICE', ...
        BUTTON_OK,BUTTON_QUIT, ...
        opts);
    switch notice
        case BUTTON_OK
        case BUTTON_QUIT                % quit
            fprintf('Quitting...'); % quitting
            quitProgram = true;
            return;                     % exit program
        otherwise                       % cancel
            fprintf('Quitting...'); % quitting
            quitProgram = true;
            return;                     % exit program
    end
    
    fprintf('Checking physical setup...'); % checking setup
    promptQuestCheck_list = {...
        'Is the Voltage Monitor connected to CH 1 of the oscilloscope?',...
        'Is the Current Monitor connected to CH 2 of the oscilloscope?',...
        'Is the oscilloscope turned on?'};
    numOfQuestCheck = length(promptQuestCheck_list);% number of questions
    for n = 1:numOfQuestCheck                       % loop through questions
        promptQuestCheck = promptQuestCheck_list{n};
        opts.Default = BUTTON_YES;       % option dedault
        questCheck = questdlg(...               % question dialog
            promptQuestCheck,...                % question prompts
            'CHECK',...                 % question litle
            BUTTON_YES,BUTTON_NO,BUTTON_QUIT,...% buttons
            opts);                              % dialog options
        % Confirmation
        switch questCheck                           % apply choice
            case BUTTON_YES                         % check
                if n == numOfQuestCheck             % completed checks
                    fprintf('OK\n');% continue
                end
            case BUTTON_NO                                      % bad answer
                promptSingleChannel = 'SETUP INCOMPLETE';                    % setup incomplete
                prompt2 = 'Answer MUST be "Yes" to continue!';  % solution
                waitfor(msgbox({promptSingleChannel,prompt2},'ERROR'));   % message
                fprintf('\nQuitting...');                     % quitting
                quitProgram = true;
                return;                                         % exit program
            case BUTTON_QUIT                % quit
                fprintf('\nQuitting...'); % quitting
                quitProgram = true;
                return;                     % exit program
            otherwise                       % cancel
                fprintf('\nQuitting...'); % quitting
                quitProgram = true;
                return;                     % exit program
        end
    end

    confirmPhysSetup = true;
end

end