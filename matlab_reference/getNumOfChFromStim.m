function [numOfChFromStim,quitProgram] = getNumOfChFromStim(numOfStim)
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
errGetNChannels = 1;
quitProgram = false;
numOfChFromStim = 0;

%% Function
while errGetNChannels == 1
    fprintf('Getting number of channels per stimulator...'); % get stimulator(s)
    for stimNum = FIRST:numOfStim                              % each stimulator
        [numOfChFromStim,errGetNChannels] = PS_GetNChannels(stimNum);	% get number of channels
        switch errGetNChannels                                          % getting errors
            case 0                                                      % no errors
                if numOfStim > 1
                fprintf('\nNumber of channels in Stimulator %d: %d\n',stimNum,numOfChFromStim);	% number of channels found
                else
                    fprintf('%d\n',numOfChFromStim);	% number of channels found
                end
            case 1                                                      % channel not found
                msg = 'INVALID ARGUMENT(S)';                            % invalid argument(s)
                quest = questdlg(msg,TITLE_ERROR,BUTTON_TRY,BUTTON_QUIT,opts);
        end
        if errGetNChannels == 1
            switch quest                            % apply choice
                case BUTTON_TRY                     % try again
                    fprintf('\nTrying again...'); % trying again
                    try
                        PS_InitAllStim();
                        pause(5);
                    catch
                    end
                case BUTTON_QUIT                    % quit
                    fprintf('\nQuitting...');     % quitting
                    quitProgram = true;
                    return;                         % exit program
                otherwise                           % cancel
                    fprintf('\nQuitting...');     % quitting
                    quitProgram = true;
                    return;                         % exit program
            end
        end
    end
    if numOfStim > 1
        fprintf('\n');
    end
end

end