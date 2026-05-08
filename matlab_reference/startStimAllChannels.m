function [quitProgram] = startStimAllChannel(stimNum)
%% Constants
FIRST = 1;
% Buttons
BUTTON_QUIT = 'Quit';
BUTTON_TRY = 'Start Over';  % start over button
TITLE_ERROR = 'ERROR';
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
quitProgram = 0;

%% Function
fprintf("All channels' stimulation...");
errStartStimChannel = 1;                                          	% initialize errors
while errStartStimChannel ~= 0                                      % start stimulating channel
    errStartStimChannel = PS_StartStimAllChannels(stimNum);  % get errors
    switch errStartStimChannel              % getting errors
        case 0                              % no errors
            fprintf('ON\n');
        case 1                                                                  % errors found
            msg = 'ERROR STARTING STIMULATION';                                 % error starting stimulation
            quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
        case 4                                                                  % errors found
            msg = 'WRONG TRIGGER MODE ON STIMULATOR';                           % wrong trigger mode
            quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
        case -1                                                                 % errors found
            msg = 'INVALID ARGUMENT(S)';                                        % invalid argument(s)
            quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
    end
    if errStartStimChannel ~= 0
        switch quest                        % apply choice
            case BUTTON_QUIT            	% quit
                fprintf('Quitting...\n\n');	% quitting
                quitProgram = 1;
                return;                     % exit program
            case BUTTON_TRY               	% try again
                fprintf('Trying again...\n\n');% trying again
            otherwise                       % cancel
                fprintf('Quitting...\n\n');	% quitting
                quitProgram = 1;
                return;                     % exit program
        end
    end
end

end