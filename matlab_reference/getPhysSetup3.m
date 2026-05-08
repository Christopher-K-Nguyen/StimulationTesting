function [File,quitProgram] = getPhysSetup3(File)
%% Constants
% Buttons
BUTTON_YES = 'Yes';
BUTTON_NO = 'No';
BUTTON_OK = 'OK';
BUTTON_QUIT = 'Quit';
% Titles
TITLE_ERROR = 'ERROR';
TITLE_NOTICE = 'NOTICE';
TITLE_QUEST_CHECK = 'Check';
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
confirmPhysSetup = false;       % initialize setup
quitProgram = false;

%% Function
while confirmPhysSetup == false         	% check setup
    % Notice
    prompt1 = 'This code ONLY works for:';
    prompt2 = '\bullet 1 PlexStim stimulator';
    prompt3 = '\bullet 1 Tektronix oscilloscope via USB';

    notice = questdlg({prompt1,prompt2,prompt3},TITLE_NOTICE,BUTTON_OK,BUTTON_QUIT,opts);
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
        {'Is the counter electrode connected to the PlexStim Analog IO GND?',...
        '(STIM OUT Ch 11 or 25)'},...
        'Is the PlexStim "V MONITOR" connected to CH 1 of the oscilloscope?',...
        'Is the PlexStim "I MONITOR" connected to CH 2 of the oscilloscope?',...
        'Is the oscilloscope turned on?',...
        'Is the PlexStim turned on?',...
        'Is the Stim-2 (Plexon Stimulator - 2.0) software closed?'};
    numOfQuestCheck = length(promptQuestCheck_list);% number of questions
    for n = 1:numOfQuestCheck                       % loop through questions
        promptQuestCheck = promptQuestCheck_list{n};
        questCheck = questdlg(...               % question dialog
            promptQuestCheck,...                % question prompts
            TITLE_QUEST_CHECK,...                 % question litle
            BUTTON_YES,BUTTON_NO,BUTTON_QUIT,...% buttons
            opts);                              % dialog options
        % Confirmation
        switch questCheck                           % apply choice
            case BUTTON_YES                         % check
                if n == numOfQuestCheck             % completed checks
                    confirmPhysSetup = true;               % setup confirmed
                    fprintf('OK.\n');% continue
                end
            case BUTTON_NO                                      % bad answer
                promptSingleChannel = 'SETUP INCOMPLETE';                    % setup incomplete
                prompt2 = 'Answer MUST be "Yes" to continue!';  % solution
                waitfor(msgbox({promptSingleChannel,prompt2},TITLE_ERROR));   % message
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
end

[File,quitProgram] = getReferenceElectrode(File,'PtIr');

end